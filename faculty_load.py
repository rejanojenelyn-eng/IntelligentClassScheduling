"""Shared faculty teaching-load computation.

Single source of truth for Regular / Part-Time / Teaching-Substitution load caps
and usage. Load is measured in ACTUAL SCHEDULED HOURS (real timeslot start/end),
never curriculum credit units. Used by both app.py (Manual Editor, Faculty Load
tab, DSS suggestions, validation) and scheduler.py (the GA and CSPValidator) so
they can't drift from each other the way the old per-file duplicate
implementations did.

`employeetype.regularload` / `parttimeload` / `teachingsubstitution` and
`designation.regularloadunit` are reinterpreted IN PLACE as hour caps (no schema
change) — the same columns that used to mean "credit units" now mean "hours".

Phase B checkpoint 1 (final HC1-HC4/HC8/HC9 load classification): `classify_assignment`
and `is_am_pt_window` are the ONE authoritative Regular-vs-PT decision, reused by
`classify_slice` below and by scheduler.CSPValidator's `_check_time_windows`,
`_check_night_pt_cap`, and `_check_load_limits`. Before this checkpoint, those
three places disagreed about whether a designee's 7:30-9:00 AM class was Regular
or PT — see the Phase B0 gap analysis. There must never be a fourth classifier.
Phase B checkpoint 3: `is_valid_merge` (final HC16) lives HERE, not in the
constraints/ package, deliberately. scheduler.py already does `import
faculty_load`; the constraints/ package's __init__.py imports scheduler.py
(via hard_constraints.py) unconditionally at package-import time. Importing
`constraints` from this module would therefore create a real circular
import that fails depending on which module happens to be imported first
(traced and confirmed during this checkpoint, not a theoretical concern) --
so this module stays a dependency-free leaf, exactly as it already was
after checkpoint 1, and scheduler.CSPValidator.is_valid_merge delegates to
the copy here instead of the reverse.
"""
from collections import defaultdict
from datetime import time

WEEKDAYS = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'}

# Display order for day/time lists (Faculty Load tab, TS panel, etc.) — sessions
# arrive from FACULTY_SESSIONS_SQL ordered by clock time, not weekday, so a
# Thursday slice that starts earlier than a Monday one would otherwise print
# "Thursday, Monday, Saturday" instead of "Monday, Thursday, Saturday".
DAY_ORDER = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
             'Friday': 4, 'Saturday': 5, 'Sunday': 6}

# ── THE effective (live) schedule ─────────────────────────────────────────────────
# Effective Schedule = Official Published
#                      MINUS Official occurrences overridden by an active Local Published
#                      PLUS  the active Local Published sessions.
# local_arrangement_sessions.official_sessionid is the authoritative Local -> Official link.
# Drafts (Official or Local) are never live. Every live reader (faculty load, faculty
# assignments / blocked times, room report, HC8) selects FROM this one definition so they
# cannot disagree. Use as `WITH {EFFECTIVE_SESSIONS_CTE} SELECT ... FROM effective_sessions es`.
#   source          'Official' | 'Local'
#   sessionid       schedule_sessions.sessionid (Official) / local_arrangement_sessions.sessionid
#   official_sessionid  the Official occurrence (itself, or the one a Local row replaces)
#   scheduleid / curriculumsubjectid  the Official subject+section+semester identity
#   employeenumber  the faculty actually teaching this live occurrence
EFFECTIVE_SESSIONS_CTE = """
    effective_sessions AS (
        SELECT 'Official'::text AS source, ss.sessionid, ss.sessionid AS official_sessionid,
               sv.versionid, sc.scheduleid, sc.sectionid, sc.semesterid, sc.curriculumsubjectid,
               COALESCE(sv.employeenumber, sc.employeenumber) AS employeenumber,
               ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid AND sv.status = 'Published'
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        WHERE NOT EXISTS (
            SELECT 1
            FROM local_arrangement la_x
            JOIN local_arrangement_sessions las_x ON las_x.arrangementid = la_x.arrangementid
            WHERE la_x.status = 'Published' AND la_x.is_active = TRUE
              AND la_x.semesterid = sc.semesterid AND la_x.sectionid = sc.sectionid
              AND las_x.official_sessionid = ss.sessionid
        )
        UNION ALL
        SELECT 'Local'::text, las.sessionid, las.official_sessionid,
               osv.versionid, osc.scheduleid, la.sectionid, la.semesterid, osc.curriculumsubjectid,
               las.faculty_employeenumber,
               las.daydesc, las.starttimeid, las.endtimeid, las.roomid
        FROM local_arrangement la
        JOIN local_arrangement_sessions las ON las.arrangementid = la.arrangementid
        JOIN schedule_sessions oss ON oss.sessionid = las.official_sessionid
        JOIN schedule_version osv ON oss.versionid = osv.versionid
        JOIN schedule osc ON osv.scheduleid = osc.scheduleid AND osc.sectionid = la.sectionid
        WHERE la.status = 'Published' AND la.is_active = TRUE
    )
"""

# Display order for day/time lists (Faculty Load tab, TS panel, etc.) — sessions
# arrive from FACULTY_SESSIONS_SQL ordered by clock time, not weekday, so a
# Thursday slice that starts earlier than a Monday one would otherwise print
# "Thursday, Monday, Saturday" instead of "Monday, Thursday, Saturday".
DAY_ORDER = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
             'Friday': 4, 'Saturday': 5, 'Sunday': 6}

_YEAR_SECTION_SQL = """
        -- Section names already embed program+year (e.g. "BSIT1") for most sections, so
        -- prefixing with programcode-yearlevel again would print "BSIT-1 BSIT1". Only
        -- prefix when the section name doesn't already start with the program code.
        CASE
            WHEN sec.sectionname IS NULL OR sec.sectionname = ''
                THEN COALESCE(pyl.programcode,'') || '-' || COALESCE(pyl.yearlevel::text,'')
            WHEN sec.sectionname ILIKE (COALESCE(pyl.programcode,'') || '%%')
                THEN sec.sectionname
            ELSE COALESCE(pyl.programcode,'') || '-' || COALESCE(pyl.yearlevel::text,'')
                     || ' ' || sec.sectionname
        END"""

# Live query: for a single faculty, every LIVE time slice (the effective schedule above)
# with its REAL elapsed hours computed from the actual assigned timeslots. Pending Drafts
# never change a faculty member's live load; a proposed Publish is validated by adding the
# candidate rows to the OTHER sections' live load (see app._other_sections_faculty_hours).
FACULTY_SESSIONS_SQL = """
    WITH """ + EFFECTIVE_SESSIONS_CTE + """
    SELECT
        cs.subjectcode,
        cs.subjectname,
        COALESCE(cs.creditunits,0) AS units,
        ROUND(EXTRACT(EPOCH FROM (ts_e.timevalue - ts_s.timevalue)) / 3600.0, 2) AS hrs,""" + _YEAR_SECTION_SQL + """ AS year_section,
        sec.sectionid,
        TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS time_range,
        LPAD(EXTRACT(HOUR FROM ts_s.timevalue)::text,2,'0') ||
        LPAD(EXTRACT(HOUR FROM ts_e.timevalue)::text,2,'0') AS time_code,
        es.daydesc AS days,
        COALESCE(r.roomname,'—') AS room,
        'Published'::text AS status,
        es.source,
        -- Merged-event identity for HC16/HC17 (group model): faculty, room id and the
        -- exact start/end as 'HH:MM' strings (JSON-safe).
        es.employeenumber AS employeenumber,
        es.roomid AS roomid,
        TO_CHAR(ts_s.timevalue,'HH24:MI') AS start,
        TO_CHAR(ts_e.timevalue,'HH24:MI') AS "end"
    FROM effective_sessions es
    JOIN curriculumsubject cs ON es.curriculumsubjectid = cs.curriculumsubjectid
    JOIN semester sem ON es.semesterid = sem.semesterid
    LEFT JOIN sections sec ON es.sectionid = sec.sectionid
    LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
    LEFT JOIN room r ON es.roomid = r.roomid
    LEFT JOIN timeslot ts_s ON es.starttimeid = ts_s.timeid
    LEFT JOIN timeslot ts_e ON es.endtimeid = ts_e.timeid
    WHERE es.employeenumber = %s
      AND sem.academicyearid = %s
      AND sem.semestertype   = %s
    ORDER BY cs.subjectcode, ts_s.timevalue
"""

# Batch variant: total real scheduled hours per faculty, for a whole set of faculty at
# once (DSS suggest, cross-program overload checks, the GA's existing load). Same live
# effective schedule. `extra_where` may reference sc (the Official schedule row of the
# occurrence), cs and c.
_BATCH_HOURS_SQL_TMPL = """
    WITH """ + EFFECTIVE_SESSIONS_CTE + """
    SELECT es.employeenumber,
           cs.subjectcode,
           es.sectionid,""" + _YEAR_SECTION_SQL + """ AS year_section,
           es.daydesc AS days,
           LPAD(EXTRACT(HOUR FROM ts_s.timevalue)::text,2,'0') ||
           LPAD(EXTRACT(HOUR FROM ts_e.timevalue)::text,2,'0') AS time_code,
           TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS time_range,
           ROUND(EXTRACT(EPOCH FROM (ts_e.timevalue - ts_s.timevalue)) / 3600.0, 2) AS hrs,
           ts_s.timevalue AS start_time,
           ts_e.timevalue AS end_time,
           pyl.programcode AS programcode,
           sec.sectionname AS sectionname,
           es.roomid AS roomid
    FROM effective_sessions es
    JOIN schedule sc ON sc.scheduleid = es.scheduleid
    JOIN curriculumsubject cs ON es.curriculumsubjectid = cs.curriculumsubjectid
    JOIN curriculum c ON cs.curriculumid = c.curriculumid
    JOIN semester sem ON es.semesterid = sem.semesterid
    LEFT JOIN sections sec ON es.sectionid = sec.sectionid
    LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
    LEFT JOIN timeslot ts_s ON es.starttimeid = ts_s.timeid
    LEFT JOIN timeslot ts_e ON es.endtimeid = ts_e.timeid
    WHERE sem.academicyearid = %(ay_id)s AND sem.semestertype = %(sem)s
      AND es.employeenumber IS NOT NULL
      {extra_where}
    ORDER BY es.employeenumber, cs.subjectcode, es.daydesc, ts_s.timevalue
"""


def is_am_pt_window(day, start_time, end_time, am_pt_start=None, am_pt_end=None):
    """True when a weekday slice falls entirely within the shared authorized
    morning extra-teaching window (default 7:30-9:00 AM). This is the ONE
    place that decision is made -- classify_assignment() below and
    scheduler.CSPValidator's HC1/HC2/HC3/HC4 window checks all call this
    (directly or via classify_assignment) so they can never disagree about
    which slices qualify."""
    am_pt_start = am_pt_start or time(7, 30)
    am_pt_end   = am_pt_end   or time(9, 0)
    if day not in WEEKDAYS or start_time is None or end_time is None:
        return False
    return start_time >= am_pt_start and end_time <= am_pt_end


def classify_assignment(day, start_time, end_time, *, is_part_time=False,
                         regular_start=None, regular_end=None,
                         am_pt_start=None, am_pt_end=None):
    """Single authoritative Regular-vs-PT classifier for one scheduled time
    slice, for ANY faculty type (full-time, designee, or part-time), given
    that faculty's own applicable regular-teaching window. Returns 'regular'
    or 'pt'.

    TS is never returned here -- Teaching Substitution is an OVERFLOW concept
    applied afterward once a faculty's totals exceed their Regular/PT caps
    (see cap_spill()), not a property of a single slice's time/day.

    The authorized morning extra-teaching window (default 7:30-9:00 AM) is
    ALWAYS 'pt', for every faculty type -- this is what fixes the designee
    gap Phase B0 identified: a designee's 7:30-9:00 class used to be
    validated as if it were a Regular-hours slot with no exception, unlike
    full-time faculty who already had one.
    """
    if is_part_time:
        return 'pt'   # Part-time faculty have no Regular bucket at all.
    if day not in WEEKDAYS:
        return 'pt'
    if is_am_pt_window(day, start_time, end_time, am_pt_start, am_pt_end):
        return 'pt'
    reg_start = regular_start or time(7, 30)
    reg_end   = regular_end   or time(16, 30)
    if start_time is not None and end_time is not None \
       and start_time >= reg_start and end_time <= reg_end:
        return 'regular'
    return 'pt'


# ── Designee time segments (final HC2/HC4 + HC9 for designees) ───────────────────
# A designee's weekday class is classified PER TIME SEGMENT, never as one block —
# it is split at the window boundaries first:
#   morning  [AM window start, AM window end)  default 7:30-9:00 AM   -> Regular (TS once Regular is full)
#   regular  [AM window end, regular end)      default 9:00 AM-4:30 PM -> Regular (TS once Regular is full)
#   pm       [regular end, designee PM end)    default 4:30-6:00 PM   -> PT/TS
#   night    [designee PM end, 9:00 PM)        default 6:00-9:00 PM   -> PT (Night Teaching Service, HC8)
#   outside  anything else on a weekday                                -> not a valid teaching time
# On weekends the whole slice is one 'weekend' segment (PT; weekend availability is the
# restricted-day rule's concern). TS is still never decided here: Regular hours beyond
# the Regular cap (and PT beyond the PT cap) spill into TS at the load step (cap_spill /
# HC9), so the same segment can be Regular or TS depending on remaining allocation.
DESIGNEE_REGULAR_KINDS = frozenset({'morning', 'regular'})
DESIGNEE_PT_KINDS      = frozenset({'pm', 'night', 'weekend'})


def _minutes(t):
    return t.hour * 60 + t.minute


def _from_minutes(m):
    return time(m // 60, m % 60)


def designee_segments(day, start_time, end_time, *, regular_start=None, regular_end=None,
                      am_pt_start=None, am_pt_end=None, pm_end=None, night_end=None):
    """[(start, end, kind)] for one designee slice — see the table above."""
    if start_time is None or end_time is None or end_time <= start_time:
        return []
    if day not in WEEKDAYS:
        return [(start_time, end_time, 'weekend')]
    am_s = am_pt_start or time(7, 30)
    am_e = am_pt_end or time(9, 0)
    first = min(am_s, regular_start or am_s)
    reg_e = regular_end or time(16, 30)
    pm_e = pm_end or time(18, 0)
    nt_e = night_end or time(21, 0)
    bounds = [(first, am_e, 'morning'), (am_e, reg_e, 'regular'),
              (reg_e, pm_e, 'pm'), (pm_e, nt_e, 'night')]
    s, e = _minutes(start_time), _minutes(end_time)
    out, cur = [], s
    for (bs, be, kind) in bounds:
        bs, be = _minutes(bs), _minutes(be)
        if be <= bs:
            continue
        lo, hi = max(cur, bs), min(e, be)
        if lo > cur:                                   # a gap before this window
            out.append((_from_minutes(cur), _from_minutes(min(lo, e)), 'outside'))
            cur = min(lo, e)
        if hi > lo:
            out.append((_from_minutes(lo), _from_minutes(hi), kind))
            cur = hi
        if cur >= e:
            break
    if cur < e:
        out.append((_from_minutes(cur), _from_minutes(e), 'outside'))
    return out


def designee_split_hours(day, start_time, end_time, **windows):
    """(regular_hours, pt_hours, outside_hours) of one designee slice."""
    reg = pt = out = 0.0
    for (s, e, kind) in designee_segments(day, start_time, end_time, **windows):
        h = (_minutes(e) - _minutes(s)) / 60.0
        if kind in DESIGNEE_REGULAR_KINDS:
            reg += h
        elif kind in DESIGNEE_PT_KINDS:
            pt += h
        else:
            out += h
    return reg, pt, out


def _designee_windows_from_config(config):
    """Window bounds for designee_segments from scheduler_config (defaults otherwise)."""
    def _t(key, default):
        raw = (config or {}).get(key)
        try:
            h, m = str(raw).split(':')[:2]
            return time(int(h), int(m))
        except (TypeError, ValueError, AttributeError):
            return default
    return {'am_pt_start': _t('hc_pt_am_start', time(7, 30)), 'am_pt_end': _t('hc_pt_am_end', time(9, 0)),
            'regular_end': _t('hc4_pt_pm_start', time(16, 30)),
            'pm_end': _t('hc4_pt_pm_end', time(18, 0))}


def _session_times(s):
    """(start, end) time objects of a load session row, from start_time/end_time,
    'HH:MM' start/end strings, or (hour-granular) time_code — None when unknown."""
    def _as_t(v):
        if v is None or hasattr(v, 'hour'):
            return v
        try:
            h, m = str(v).split(':')[:2]
            return time(int(h), int(m))
        except (TypeError, ValueError):
            return None
    st = _as_t(s.get('start_time')) or _as_t(s.get('start'))
    et = _as_t(s.get('end_time')) or _as_t(s.get('end'))
    if st is None or et is None:
        tc = str(s.get('time_code') or '')
        try:
            st, et = time(int(tc[:2]), 0), time(min(int(tc[2:]), 23), 0)
        except (TypeError, ValueError):
            return None, None
    return st, et


def _designee_session_split(s, hrs, config):
    """(regular_hrs, pt_hrs) for a load session of a DESIGNEE: its credited `hrs` split in
    the proportion of its time segments (outside time counts as PT, as before)."""
    st, et = _session_times(s)
    if st is None or et is None or hrs <= 0:
        reg = is_reg_slice(s.get('days'), s.get('time_code'))
        return (hrs, 0.0) if reg else (0.0, hrs)
    r, p, o = designee_split_hours(s.get('days') or s.get('day'), st, et,
                                   **_designee_windows_from_config(config))
    total = r + p + o
    if total <= 0:
        return 0.0, hrs
    return hrs * r / total, hrs * (p + o) / total


def classify_slice(day, end_hour, start_hour=None):
    """Hour-granularity wrapper around classify_assignment(), kept for the
    existing Faculty Load tab / group_assignments() call sites which only
    have whole-hour time_code data (not full datetime.time objects) and no
    per-faculty regular-window override on hand. Delegates to
    classify_assignment() with this function's own long-standing defaults
    (4:00 PM regular-end cutoff, 7:30-9:00 AM window) once both hours are
    known; falls back to the plain end-hour-only rule when start_hour is
    absent, exactly as before.
    """
    try:
        end_hour = int(end_hour)
    except (TypeError, ValueError):
        end_hour = 0
    if day not in WEEKDAYS:
        return 'pt'
    if start_hour is None:
        return 'regular' if end_hour <= 16 else 'pt'
    try:
        start_hour = int(start_hour)
    except (TypeError, ValueError):
        return 'regular' if end_hour <= 16 else 'pt'
    start_t = time(min(max(start_hour, 0), 23), 0)
    end_t   = time(min(max(end_hour, 0), 23), 59 if end_hour >= 24 else 0)
    # regular_start=00:00 and am_pt_start=00:00 deliberately reproduce this
    # function's own original condition exactly: "start_hour < 9 and
    # end_hour <= 9" -> PT (hour-truncated, so a real 7:30 start arrives here
    # as hour 7 -- a lower bound of 7:30 would wrongly miss it), else
    # "end_hour <= 16" -> Regular. Verified byte-for-byte equal to the
    # pre-refactor function across every start<end hour pair a real
    # schedule can produce (see the Phase B checkpoint report).
    return classify_assignment(day, start_t, end_t, regular_start=time(0, 0),
                                regular_end=time(16, 0),
                                am_pt_start=time(0, 0), am_pt_end=time(9, 0))


def _time_code_end_hour(time_code):
    try:
        return int(str(time_code or '')[2:])
    except ValueError:
        return 0


def _time_code_start_hour(time_code):
    try:
        return int(str(time_code or '')[:2])
    except ValueError:
        return 0


def is_reg_slice(days, time_code):
    return classify_slice(days, _time_code_end_hour(time_code), _time_code_start_hour(time_code)) == 'regular'


def get_faculty_caps(faculty_row):
    """faculty_row needs: designationid, plus Regular/PT/TS caps and a type label under any
    of the several key-name conventions the existing call sites already use (regularload/
    reg_load, parttimeload/pt_load, teachingsubstitution/teach_sub, designation_regular_load/
    desig_reg_load, typename/employee_type/employeestatus). Returns (reg_max, pt_max, ts_max)
    in HOURS — same lookup rule as before (designation overrides Regular only; PT/TS always
    come from the faculty's own employeetype; a designation's nightteachingservice is a
    separate "nights on duty" cap, never load).

    Designee TS transfer (July 2026): a designation's Regular Load Hours REPLACES the
    faculty's plain employeetype Regular Load (e.g. Designee default 9 -> Academic Head 6),
    but the faculty's TOTAL allowable hours must stay whatever their own employeetype's
    Regular+PT+TS already sums to (e.g. Designee 9+12+19=40) — never a hardcoded 40, always
    derived from their own employeetype row so it keeps working if admin changes it. Any
    hours a designation REMOVES from Regular are added onto TS (and any hours a designation
    ADDS to Regular beyond the default are removed from TS) so Regular+PT+TS is invariant;
    PT is never touched by this transfer.
    """
    g = faculty_row.get
    has_desig = g('designationid') is not None
    typename = (g('typename') or g('employee_type') or g('employeestatus') or '').lower()
    pt_max = float(g('parttimeload') or g('pt_load') or 0)
    ts_max = float(g('teachingsubstitution') or g('teach_sub') or 0)
    if has_desig:
        default_reg_max = float(g('regularload') or g('reg_load') or 0)
        reg_max = float(g('designation_regular_load') or g('desig_reg_load') or 0)
        ts_max += (default_reg_max - reg_max)
    elif 'part' in typename:
        reg_max = 0.0
    else:
        reg_max = float(g('regularload') or g('reg_load') or 0)
    return reg_max, pt_max, ts_max


def get_faculty_sessions(cur, emp_num, ay_id, sem, published_only=False):
    """One faculty's LIVE sessions (effective schedule). `published_only` is kept for
    call-site compatibility: the live schedule never includes Drafts, so it no longer
    changes anything."""
    cur.execute(FACULTY_SESSIONS_SQL, (emp_num, ay_id, sem))
    return [dict(r) for r in (cur.fetchall() or [])]


def _default_config(config):
    if config is None:
        from database import load_scheduler_config
        config = load_scheduler_config()
    return config


def _merge_policy_of(config):
    """The HC16/HC17 group-model policy carried by `config` (merge_groups), or None
    under the legacy model — then every legacy HC17 path below runs unchanged.
    Imported lazily: merge_groups imports this module."""
    if not config:
        return None
    import merge_groups
    return merge_groups.policy_from_config(config)


def _load_config(config, cur, ay_id, sem):
    """`config` for a load computation of one term. Under the group model, when the
    caller did not inject a policy, the semester's HC16 Merge Groups + HC17 policy
    mappings are loaded here so totals always follow merged-event identity."""
    config = _default_config(config)
    import merge_groups
    if (merge_groups.merge_model(config) != merge_groups.MODEL_GROUPS
            or isinstance(config.get(merge_groups.POLICY_CFG_KEY), merge_groups.GroupMergePolicy)):
        return config
    cur.execute("SELECT semesterid FROM semester WHERE academicyearid = %s AND semestertype = %s LIMIT 1",
                (ay_id, sem))
    row = cur.fetchone()
    sem_id = (row['semesterid'] if isinstance(row, dict) else row[0]) if row else None
    return merge_groups.with_policy(config, merge_groups.policy_for(config, [sem_id], cur=cur))


def get_faculty_scheduled_hours(cur, emp_num, ay_id, sem, published_only=False, config=None):
    """Total real LIVE scheduled hours for one faculty (effective schedule: Published
    Official + active Published Local), plus the raw session rows (callers that only need
    the total can ignore the second value). `published_only` is accepted for compatibility.

    HC17: the total is merge-aware (group_assignments with the live merge policy),
    so a same-faculty merged class stored once per section counts once -- the
    same total the Faculty Load tab and CSPValidator's HC9 use. The raw sessions
    are returned unchanged."""
    sessions = get_faculty_sessions(cur, emp_num, ay_id, sem, published_only=published_only)
    grouped = group_assignments(sessions, config=_load_config(config, cur, ay_id, sem))
    return round(sum(float(g.get('hrs') or 0) for g in grouped), 2), sessions


def _fetch_batch_rows(cur, ay_id, sem, exclude_program=None, exclude_year_level=None,
                      exclude_section_id=None):
    """Draft-preferred per-slice rows for every faculty this term, minus the
    schedule being validated. When the current section is known only THAT
    section is excluded, so sibling sections of the same program/year still
    count toward a faculty's load; otherwise the legacy program+year exclusion
    applies (callers that have no section identity)."""
    extra_where = ''
    params = {'ay_id': ay_id, 'sem': sem}
    if exclude_section_id not in (None, ''):
        extra_where = 'AND sc.sectionid IS DISTINCT FROM %(excl_sec)s'
        params['excl_sec'] = int(exclude_section_id)
    elif exclude_program and exclude_year_level is not None:
        extra_where = 'AND NOT (UPPER(c.programcode) = %(excl_prog)s AND cs.yearlevel = %(excl_yl)s)'
        params['excl_prog'] = exclude_program.upper()
        params['excl_yl'] = int(exclude_year_level)
    cur.execute(_BATCH_HOURS_SQL_TMPL.format(extra_where=extra_where), params)
    return [dict(r) for r in (cur.fetchall() or [])]


def summarize_faculty_load(sessions, config=None, designee=False):
    """One faculty's HC9 committed load from per-slice session rows, HC17
    merge-aware and split into the same Regular / PT buckets group_assignments
    uses:

        {'regular': h, 'pt': h, 'total': h, 'slices': [...]}

    `slices` are the underlying meetings (subject, day, start/end, section
    identity) so a validator can recognise a gene that is the SAME merged
    meeting as one of these and not count it a second time."""
    grouped = group_assignments(sessions, config=_default_config(config), designee=designee)
    reg = round(sum(float(g.get('_reg_hrs') or 0) for g in grouped), 2)
    pt = round(sum(float(g.get('_pt_hrs') or 0) for g in grouped), 2)
    slices = [{
        'faculty_id':   s.get('employeenumber'),
        'subject_code': s.get('subjectcode'),
        'day':          s.get('days'),
        'start_time':   s.get('start_time'),
        'end_time':     s.get('end_time'),
        'programcode':  s.get('programcode'),
        'section_name': s.get('sectionname'),
        'sectionid':    s.get('sectionid'),
        # group model (HC16/HC17): the slice's room, its own real hours and, for rows
        # that carry times only as 'HH:MM' strings (FACULTY_SESSIONS_SQL), those times
        'roomid':       s.get('roomid'),
        'hrs':          s.get('hrs'),
        'start':        s.get('start'),
        'end':          s.get('end'),
    } for s in sessions]
    return {'regular': reg, 'pt': pt, 'total': round(reg + pt, 2), 'slices': slices}


def _designee_ids(cur):
    """{employeenumber} of every faculty member who holds a designation."""
    try:
        cur.execute("SELECT employeenumber FROM faculty WHERE designationid IS NOT NULL")
        return {str(r['employeenumber'] if isinstance(r, dict) else r[0]) for r in (cur.fetchall() or [])}
    except Exception:
        return set()


def get_faculty_load_batch(cur, ay_id, sem, exclude_program=None, exclude_year_level=None,
                           exclude_section_id=None, config=None):
    """{employeenumber: summarize_faculty_load(...)} for the whole term -- the
    bucketed, merge-aware `existing_load` CSPValidator's HC9 consumes."""
    config = _load_config(config, cur, ay_id, sem)
    by_faculty = defaultdict(list)
    for row in _fetch_batch_rows(cur, ay_id, sem, exclude_program, exclude_year_level,
                                 exclude_section_id):
        by_faculty[row.get('employeenumber')].append(row)
    # Designees' sessions are split by time segment (designee_segments) into Regular/PT.
    designees = _designee_ids(cur)
    return {emp: summarize_faculty_load(sessions, config, designee=str(emp) in designees)
            for emp, sessions in by_faculty.items()}


def get_faculty_hours_batch(cur, ay_id, sem, exclude_program=None, exclude_year_level=None,
                            config=None, exclude_section_id=None):
    """Total real scheduled hours per faculty across the term, HC17 merge-aware.

    The old batch query summed schedule rows directly, which meant a same-faculty
    merged class represented once per section was double/triple counted here even
    though CSPValidator._check_load_limits and the Faculty Load detail path counted
    it once.  Return detailed slices and run them through group_assignments so every
    HC9 consumer sees the same merged-load total.

    Different-faculty NSTP/OU shared sessions remain independent naturally because
    rows are grouped by employeenumber before HC17 deduplication.
    """
    loads = get_faculty_load_batch(cur, ay_id, sem, exclude_program, exclude_year_level,
                                   exclude_section_id, config)
    return {emp: summary['total'] for emp, summary in loads.items()}


def load_total(value):
    """Total committed hours from an existing-load value, which is either the
    bucketed dict from summarize_faculty_load or a legacy plain number."""
    if isinstance(value, dict):
        if value.get('total') is not None:
            return float(value['total'])
        return float(value.get('regular') or 0) + float(value.get('pt') or 0)
    return float(value or 0)


def _meeting_days(row):
    days = row.get('days_list') or [row.get('day')]
    if not isinstance(days, (list, tuple)):
        days = [days]
    return [d for d in days if d]


def _meeting_hours(row):
    start, end = row.get('start_time'), row.get('end_time')
    if start is None or end is None:
        return 0.0
    mins = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return max(0.0, mins / 60.0)


def merge_aware_additional_hours(rows, existing_slices=(), config=None):
    """Hours that ONE faculty's submitted `rows` add on top of their existing
    committed load, HC17-aware, one meeting at a time:

      * a meeting that is a valid HC16 merge with a meeting already counted in
        `existing_slices` (another section) adds 0 -- that physical class is
        already in the existing total;
      * several submitted rows forming one valid merged meeting add its hours once;
      * anything else adds its hours per row.

    Every participant pair must be a valid merge (3+ sections need the full
    clique, same rule as CSPValidator._check_load_limits/group_assignments).
    Rows carry subject_code, day/days_list, start_time/end_time (time objects)
    and optional course/programcode + section_name for section-pair rules.

    Group model: no inference here — the shared HC17 calculation
    (merge_groups.GroupMergePolicy.credit_rows, driven by HC16 event identity) is run
    over existing + submitted rows and over existing rows alone; the difference is
    what the submission adds (existing rows first, so an event already counted in
    another section is not counted again)."""
    cfg = config or {}
    policy = _merge_policy_of(cfg)
    if policy is not None:
        def _hours(r):
            if r.get('hrs') is not None:
                return float(r.get('hrs') or 0)
            return _meeting_hours(r) * max(1, len(_meeting_days(r)))
        existing = list(existing_slices or [])
        both = sum(m['credit'] for m in policy.credit_rows(existing + list(rows or []), hours=_hours))
        alone = sum(m['credit'] for m in policy.credit_rows(existing, hours=_hours))
        return round(both - alone, 2)
    pairs = parse_merge_section_pairs(cfg.get('hc_merge_section_pairs'))

    def _code(r):
        return (r.get('subject_code') or r.get('subjectcode') or '').upper()

    def _clique(members):
        return all(is_valid_merge(a, b, cfg, pairs)
                   for i, a in enumerate(members) for b in members[i + 1:])

    meetings = defaultdict(list)
    for r in rows or []:
        for d in _meeting_days(r):
            meetings[(_code(r), d, r.get('start_time'), r.get('end_time'))].append(r)

    total = 0.0
    for (code, day, start, end), members in meetings.items():
        hrs = _meeting_hours(members[0])
        # Only this faculty's own already-counted meeting can absorb these hours:
        # a different-faculty NSTP/OU shared session is HC16-valid operationally,
        # but each instructor keeps their own teaching hours.
        fid = members[0].get('faculty_id')
        partners = [s for s in (existing_slices or [])
                    if s.get('faculty_id') == fid
                    and (s.get('subject_code') or '').upper() == code and s.get('day') == day
                    and s.get('start_time') == start and s.get('end_time') == end]
        if partners and _clique(list(members) + partners):
            continue
        if len(members) > 1 and _clique(members):
            total += hrs
        else:
            total += hrs * len(members)
    return round(total, 2)


def get_subject_nominal_hours(subject_row):
    """Catalog nominal hours (tuitionhours, falling back to lecturehours+laboratoryhours).
    NOT real scheduled time — use only where a real day/time doesn't exist yet, e.g. the
    GA's gene-construction pre-filter before a candidate's slot has been chosen."""
    th = subject_row.get('tuitionhours')
    if th:
        return float(th)
    lh = float(subject_row.get('lecturehours') or 0)
    lab = float(subject_row.get('laboratoryhours') or 0)
    return lh + lab


# ── Final HC16 Merged-Class Validity (shared with scheduler.CSPValidator) ──

def parse_merge_section_pairs(raw):
    """Parse the hc_merge_section_pairs scheduler_config value (JSON array of
    ["PROGRAMCODE-SECTIONNAME", "PROGRAMCODE-SECTIONNAME"] pairs) into a set
    of unordered frozensets, so either ordering matches. Empty/unparsable ->
    empty set (no pairs configured)."""
    import json as _json
    pairs = set()
    try:
        for pair in _json.loads(raw or '[]'):
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] and pair[1]:
                pairs.add(frozenset((str(pair[0]).upper(), str(pair[1]).upper())))
    except (TypeError, ValueError):
        pass
    return pairs


def _section_pair_allowed_by_label(label_a, label_b, merge_section_pairs):
    """Core of the Class Merging Policy's section-pair check, operating on
    already-extracted 'PROGRAMCODE-SECTIONNAME' labels (or None when
    identity is unknown) rather than raw gene/session dicts -- shared by
    is_valid_merge() below (scheduler.py gene dicts) and group_assignments()
    (this module's own SQL-row labels) so the pair-matching decision is
    never reimplemented a second time."""
    if not merge_section_pairs:
        return True  # nothing configured yet -- don't newly restrict existing behavior
    if not label_a or not label_b:
        return True  # identity unknown on a side -- can't evaluate, allow through
    if label_a == label_b:
        return True  # same section, e.g. two time-slices of the same assignment
    return frozenset((label_a, label_b)) in merge_section_pairs


def is_merge_in_scope(subject_code, config=None):
    """Is this subject code within the configured Class Merging Policy scope
    (hc_merge_enabled / hc_merge_scope / hc_merge_scope_subjects)? Shared by
    is_valid_merge() below and group_assignments()'s own merge detection."""
    # Keep this pure/load-layer helper independent of database.py so HC16/HC17
    # can be tested without a PostgreSQL driver and load computation does not
    # acquire a hidden DB dependency merely to parse configuration JSON.
    import json as _json
    cfg = config or {}
    if not bool(cfg.get('hc_merge_enabled', 1)):
        return False
    code_u = (subject_code or '').upper()
    is_nstp = code_u.startswith(('NSTP', 'OU'))
    raw_scope = cfg.get('hc_merge_scope_subjects')
    scope_subjects = None
    if raw_scope:
        try:
            parsed = _json.loads(raw_scope) if isinstance(raw_scope, str) else raw_scope
            if isinstance(parsed, list) and parsed:
                scope_subjects = {str(c).upper() for c in parsed}
        except (TypeError, ValueError):
            scope_subjects = None
    if scope_subjects is not None:
        return code_u in scope_subjects or ('NSTP' in scope_subjects and is_nstp)
    merge_scope = str(cfg.get('hc_merge_scope', 'nstp_only'))
    return (
        (merge_scope == 'nstp_only'    and is_nstp) or
        (merge_scope == 'non_nstp'     and not is_nstp) or
        (merge_scope == 'all_subjects')
    )


def _gene_section_label(cls):
    """'PROGRAMCODE-SECTIONNAME' identity for a scheduler.py gene dict, or
    None when it carries no section identity. Mirrors
    scheduler.CSPValidator._section_label exactly (that method is left
    in place, still used by HC12's _same_section -- this is a standalone
    copy for is_valid_merge's use here, not a behavior change to HC12)."""
    prog = cls.get('course') or cls.get('program') or cls.get('programcode')
    sect = cls.get('section_name') or cls.get('sectionname')
    if not prog or not sect:
        return None
    return f"{str(prog).upper()}-{sect}"


def is_valid_merge(a: dict, b: dict, config: dict = None, merge_section_pairs=None) -> bool:
    """Final HC16 (Merged-Class Validity) -- the ONE authoritative merge/
    shared-class decision for scheduler.py gene dicts: same subject, in the
    configured merge scope, an appropriate faculty-sharing rule (NSTP/OU
    sections may share different faculty; any other eligible subject
    requires the SAME faculty), and an explicitly allowed section pairing
    when one is configured.

    scheduler.CSPValidator.is_valid_merge delegates here (passing its own
    already-parsed `merge_section_pairs` to avoid re-parsing JSON on every
    pairwise call in the GA's hot fitness loop) instead of this module
    importing scheduler.py -- see the module docstring for why.

    Does NOT check room equality -- that's HC11's own concern.
    """
    a_code = (a.get('subject_code') or a.get('subjectcode') or '').upper()
    b_code = (b.get('subject_code') or b.get('subjectcode') or '').upper()
    if not a_code or a_code != b_code:
        return False
    cfg = config or {}
    if not is_merge_in_scope(a_code, cfg):
        return False
    is_nstp = a_code.startswith(('NSTP', 'OU'))
    same_fac = a.get('faculty_id') == b.get('faculty_id')
    if not (is_nstp or same_fac):
        return False
    if merge_section_pairs is None:
        merge_section_pairs = parse_merge_section_pairs(cfg.get('hc_merge_section_pairs'))
    return _section_pair_allowed_by_label(
        _gene_section_label(a), _gene_section_label(b), merge_section_pairs
    )


NSTP_SHARED_PREFIXES = ('NSTP', 'OU')


def has_assigned_faculty(faculty_id) -> bool:
    """False for a missing/TBA instructor. An unassigned faculty is not a
    person and can never be double-booked (HC10), mirroring HC11's TBA-room rule."""
    return bool(faculty_id) and str(faculty_id).strip().upper() not in ('TBA', 'NONE', 'NULL')


def nstp_shared_faculty_exempt(a: dict, b: dict) -> bool:
    """Long-standing HC10 exemption, independent of merge policy: one faculty
    may cover two overlapping NSTP/OU groups (even different NSTP/OU subjects).
    Unlike is_valid_merge it is not gated by hc_merge_enabled/scope."""
    a_code = (a.get('subject_code') or a.get('subjectcode') or '').upper()
    b_code = (b.get('subject_code') or b.get('subjectcode') or '').upper()
    return a_code.startswith(NSTP_SHARED_PREFIXES) and b_code.startswith(NSTP_SHARED_PREFIXES)


def faculty_overlap_exempt(a: dict, b: dict, config: dict = None,
                           merge_section_pairs=None, valid_merge=None) -> bool:
    """THE shared HC10 exemption used by CSPValidator, the cross-schedule (HC15)
    check and Local Scheduler validation: an overlap of the same faculty is not
    a double-booking when the two occurrences are one valid HC16 merged/shared
    class (merge scope + section-pair rules apply) OR the NSTP/OU shared-faculty
    exemption applies. Pass `valid_merge` when the caller already computed the
    HC16 decision. Never exempts HC12: that remains the caller's section check."""
    if valid_merge is None:
        valid_merge = is_valid_merge(a, b, config=config, merge_section_pairs=merge_section_pairs)
    return bool(valid_merge) or nstp_shared_faculty_exempt(a, b)


def _session_bucket_hours(s, h, designee, config):
    """(regular_hrs, pt_hrs) of one load session worth `h` credited hours: a designee's
    session is split by time segment (designee_segments); anyone else's is classified
    whole, exactly as before (is_reg_slice)."""
    if designee:
        return _designee_session_split(s, h, config)
    return (h, 0.0) if is_reg_slice(s.get('days'), s.get('time_code')) else (0.0, h)


def group_assignments(sessions, config=None, designee=False):
    """Merge multi-slice rows of the same (subjectcode, year_section) into one assignment,
    tracking hour totals split by Regular-time vs PT-time slices. Mirrors the JS
    `_flGroupSessions` algorithm this module replaces so nothing can diverge again.
    Each input session dict needs: subjectcode, year_section, days, time_code, time_range, hrs.

    Phase B checkpoint 3 (HC17 consistency): when `config` is supplied, a
    valid merged/shared class (final HC16 -- same subject, in scope, and an
    allowed section pairing) that appears as separate rows per participating
    section (the schema requires one `schedule` row per section; there is no
    single shared row to begin with) has its hours counted ONCE, matching
    scheduler.CSPValidator's HC17 fix in _check_load_limits. Without
    `config` (the previous behavior), no dedup is attempted -- any existing
    caller that doesn't pass it keeps exactly today's behavior.

    Group model (config carries a merge_groups policy): see
    _group_assignments_by_event — no subject/day/time inference at all.
    """
    policy = _merge_policy_of(config)
    if policy is not None:
        return _group_assignments_by_event(sessions, policy, designee=designee, config=config)
    skip_hours = [False] * len(sessions)
    if config is not None:
        merge_section_pairs = parse_merge_section_pairs(config.get('hc_merge_section_pairs'))
        by_signature = defaultdict(list)
        for i, s in enumerate(sessions):
            sig = (s.get('subjectcode'), s.get('days'), s.get('time_code'))
            by_signature[sig].append(i)
        for (subj_code, _days, _tc), idxs in by_signature.items():
            if len(idxs) < 2 or not is_merge_in_scope(subj_code, config):
                continue

            # HC17 must use the SAME pairwise authorization semantics as HC16.
            # For 3+ sections this is a clique check: A-B + A-C is NOT enough
            # when B-C is forbidden.  The old first-vs-rest shortcut could
            # under-count an invalid three-section overlap as one teaching load.
            distinct = []
            seen_sections = set()
            for idx in idxs:
                label = str(sessions[idx].get('year_section') or '').upper() or None
                section_identity = sessions[idx].get('sectionid') or label
                if section_identity in seen_sections:
                    continue  # another slice/duplicate for the same section is not a new merge member
                seen_sections.add(section_identity)
                distinct.append((idx, label))
            if len(distinct) < 2:
                continue

            all_pairs_allowed = all(
                _section_pair_allowed_by_label(label_a, label_b, merge_section_pairs)
                for pos, (_idx_a, label_a) in enumerate(distinct)
                for _idx_b, label_b in distinct[pos + 1:]
            )
            if not all_pairs_allowed:
                continue

            # This function is called with one faculty's sessions.  Therefore a
            # different-faculty NSTP/OU shared session is naturally counted once
            # PER FACULTY, while multiple sections taught by this same faculty are
            # deduplicated to one physical teaching event.
            keeper = distinct[0][0]
            member_indexes = {idx for idx, _label in distinct}
            for other in idxs:
                if other != keeper and other in member_indexes:
                    skip_hours[other] = True

    groups = {}
    order = []
    for i, s in enumerate(sessions):
        key = (s.get('subjectcode'), s.get('year_section'))
        h = 0.0 if skip_hours[i] else float(s.get('hrs') or 0)
        rh, ph = _session_bucket_hours(s, h, designee, config)
        pair = (s.get('days'), s.get('time_range'))
        if key not in groups:
            g = dict(s)
            g['_reg_hrs'] = rh
            g['_pt_hrs'] = ph
            g['_days'] = [s.get('days')] if s.get('days') else []
            g['_times'] = [s.get('time_range')] if s.get('time_range') else []
            g['_reg_pairs'] = [pair] if (rh > 0 and s.get('days')) else []
            g['_pt_pairs'] = [pair] if (ph > 0 and s.get('days')) else []
            g['hrs'] = h
            groups[key] = g
            order.append(key)
        else:
            g = groups[key]
            g['hrs'] += h
            g['_reg_hrs'] += rh
            g['_pt_hrs'] += ph
            if s.get('days'):
                g['_days'].append(s.get('days'))
                g['_times'].append(s.get('time_range'))
                if rh > 0:
                    g['_reg_pairs'].append(pair)
                if ph > 0:
                    g['_pt_pairs'].append(pair)

    def _join(pairs):
        ordered = sorted(pairs, key=lambda p: DAY_ORDER.get(p[0], 7))
        return ', '.join(p[0] for p in ordered), ', '.join(p[1] for p in ordered)

    result = []
    for key in order:
        g = groups[key]
        # Keep each day paired with its own time range while reordering Mon->Sun,
        # regardless of the clock-time order the SQL rows arrived in.
        pairs = sorted(zip(g['_days'], g['_times']), key=lambda p: DAY_ORDER.get(p[0], 7))
        g['days'] = ', '.join(p[0] for p in pairs)
        g['time_range'] = ', '.join(p[1] for p in pairs)
        g['reg_days'], g['reg_time_range'] = _join(g['_reg_pairs'])
        g['pt_days'], g['pt_time_range'] = _join(g['_pt_pairs'])
        result.append(g)
    return result


def _group_assignments_by_event(sessions, policy, designee=False, config=None):
    """group_assignments() under the group merge model.

    Hours come ONLY from the shared HC17 calculation (policy.credit_rows), which
    consumes HC16 merged-event identity: rows of the same valid event count once for
    the faculty, an explicitly mapped HC17 policy may re-credit the shared assignment
    from curriculum hours, everything else is a normal assignment. Rows of one
    faculty's merged assignment (same Merge Group) become ONE entry: sections joined,
    subject units once (the policy's curriculum units when a policy applies), every
    meeting's day/time listed once. Normal rows keep today's (subject, year_section)
    grouping, so subject units are still credited once per assignment however many
    weekly slices it has."""
    meta = policy.credit_rows(sessions)
    labels, rooms = defaultdict(list), defaultdict(list)
    for s, m in zip(sessions, meta):
        if m['merged_group'] is None:
            continue
        key = ('MG', m['merged_group'], str(s.get('employeenumber') or s.get('faculty_id') or ''))
        if s.get('year_section') and s.get('year_section') not in labels[key]:
            labels[key].append(s.get('year_section'))
        if s.get('room') and s.get('room') not in rooms[key]:
            rooms[key].append(s.get('room'))

    groups, order = {}, []
    for s, m in zip(sessions, meta):
        merged = m['merged_group'] is not None
        if merged:
            key = ('MG', m['merged_group'], str(s.get('employeenumber') or s.get('faculty_id') or ''))
        else:
            key = (s.get('subjectcode'), s.get('year_section'))
        h = float(m['credit'])
        rh, ph = _session_bucket_hours(s, h, designee, config)
        lists_meeting = bool(s.get('days')) and (not merged or m['keeper'])
        pair = (s.get('days'), s.get('time_range'))
        if key not in groups:
            g = dict(s)
            g['_reg_hrs'] = rh
            g['_pt_hrs'] = ph
            g['_days'] = [s.get('days')] if lists_meeting else []
            g['_times'] = [s.get('time_range')] if lists_meeting else []
            g['_reg_pairs'] = [pair] if (rh > 0 and lists_meeting) else []
            g['_pt_pairs'] = [pair] if (ph > 0 and lists_meeting) else []
            g['hrs'] = h
            if merged:
                grp = policy.index.group(m['merged_group']) or {}
                g['year_section'] = ', '.join(labels[key]) or s.get('year_section')
                if rooms[key]:
                    g['room'] = ', '.join(rooms[key])
                g['merge_group'] = grp.get('groupname')
                g['merged_sections'] = m['section_count']
                rule = m['rule'] or {}
                g['merge_policy'] = rule.get('policyname') or rule.get('subjectcode')
                if m['units'] is not None:
                    g['units'] = m['units']
            groups[key] = g
            order.append(key)
        else:
            g = groups[key]
            g['hrs'] += h
            g['_reg_hrs'] += rh
            g['_pt_hrs'] += ph
            if lists_meeting:
                g['_days'].append(s.get('days'))
                g['_times'].append(s.get('time_range'))
                if rh > 0:
                    g['_reg_pairs'].append(pair)
                if ph > 0:
                    g['_pt_pairs'].append(pair)

    def _join(pairs):
        ordered = sorted(pairs, key=lambda p: DAY_ORDER.get(p[0], 7))
        return ', '.join(p[0] for p in ordered), ', '.join(p[1] for p in ordered)

    result = []
    for key in order:
        g = groups[key]
        g['hrs'] = round(g['hrs'], 4)
        g['_reg_hrs'] = round(g['_reg_hrs'], 4)
        g['_pt_hrs'] = round(g['_pt_hrs'], 4)
        pairs = sorted(zip(g['_days'], g['_times']), key=lambda p: DAY_ORDER.get(p[0], 7))
        g['days'] = ', '.join(p[0] for p in pairs)
        g['time_range'] = ', '.join(p[1] for p in pairs)
        g['reg_days'], g['reg_time_range'] = _join(g['_reg_pairs'])
        g['pt_days'], g['pt_time_range'] = _join(g['_pt_pairs'])
        result.append(g)
    return result


def credited_session_hours(sessions, config=None):
    """Per-row hours as they count toward the faculty's load: the shared HC17
    credit under the group model, the row's own hours otherwise (legacy callers
    that show raw per-day hours keep doing exactly that)."""
    policy = _merge_policy_of(config)
    if policy is None:
        return [float(s.get('hrs') or 0) for s in sessions]
    return [m['credit'] for m in policy.credit_rows(sessions)]


def cap_spill(items, max_hours):
    """Fill `items` (each with an 'hrs' float) up to max_hours; an item that only partly
    fits is SPLIT — the portion that fits stays, only the excess spills into the returned
    `spilled` list (destined for TS). Mirrors the JS `_flCapSpill`."""
    running = 0.0
    kept, spilled = [], []
    for it in items:
        h = float(it.get('hrs') or 0)
        remaining = round(max_hours - running, 2)
        if remaining <= 0:
            spilled.append(it)
        elif h <= remaining:
            running += h
            kept.append(it)
        else:
            frac = remaining / h if h > 0 else 0
            running = max_hours
            k = dict(it); k['hrs'] = round(remaining, 2); k['_split'] = True
            sp = dict(it); sp['hrs'] = round(h - remaining, 2); sp['_split'] = True
            kept.append(k)
            spilled.append(sp)
    return kept, spilled


def compute_load_buckets(sessions, faculty_row, config=None):
    """Given a faculty's raw per-slice session rows and their cap-lookup row,
    return Regular/PT/TS hour usage. This is the single source of truth
    reused by the Faculty Load tab, the assignment-time validation gate, the
    Reassign side panel, DSS "suggest faculty", and the GA's post-hoc HC9
    check — replaces the old JS-only `_computeFacultyLoadBuckets`.

    Phase B checkpoint 3: pass `config` (scheduler_config, e.g. from
    load_scheduler_config()) so a valid merged/shared class's hours are
    counted once rather than once per participating section, matching
    scheduler.CSPValidator's HC17 fix -- see group_assignments(). Omitting
    `config` keeps the previous (pre-checkpoint-3) behavior exactly.
    """
    reg_max, pt_max, ts_max = get_faculty_caps(faculty_row)
    g = faculty_row.get
    typename = (g('typename') or g('employee_type') or g('employeestatus') or '').lower()
    is_part_time = 'part' in typename and g('designationid') is None
    is_designee = g('designationid') is not None

    if is_part_time:
        # Part-time faculty have no separate daytime-duty bucket — every session is PT
        # first; only overflow beyond ptMax spills into TS.
        grouped = group_assignments(sessions, config=config)
        pt_kept, pt_spilled = cap_spill(grouped, pt_max)
        grouped_reg, grouped_pt, ts_sessions = [], pt_kept, pt_spilled
    else:
        grouped = group_assignments(sessions, config=config, designee=is_designee)
        reg_candidates, pt_candidates = [], []
        for g in grouped:
            total = g['_reg_hrs'] + g['_pt_hrs']
            if g['_pt_hrs'] <= 0:
                reg_candidates.append(g)
            elif g['_reg_hrs'] <= 0:
                pt_candidates.append(g)
            else:
                # Mixed assignment (e.g. weekday lecture + Saturday lab) — split
                # proportionally by hour-share rather than double-counting the whole
                # assignment into both buckets.
                r = dict(g); r['hrs'] = round(g['_reg_hrs'], 2); r['_split'] = True
                r['days'] = g.get('reg_days') or r['days']
                r['time_range'] = g.get('reg_time_range') or r['time_range']
                p = dict(g); p['hrs'] = round(g['_pt_hrs'], 2); p['_split'] = True
                p['days'] = g.get('pt_days') or p['days']
                p['time_range'] = g.get('pt_time_range') or p['time_range']
                reg_candidates.append(r)
                pt_candidates.append(p)
        reg_kept, reg_spilled = cap_spill(reg_candidates, reg_max)
        pt_kept, pt_spilled = cap_spill(pt_candidates, pt_max)
        grouped_reg, grouped_pt = reg_kept, pt_kept
        ts_sessions = reg_spilled + pt_spilled

    def _sum(items):
        return round(sum(float(i.get('hrs') or 0) for i in items), 2)

    reg_used, pt_used, ts_used = _sum(grouped_reg), _sum(grouped_pt), _sum(ts_sessions)
    return {
        'isPartTime': is_part_time,
        'isDesignee': is_designee,
        'regMax': reg_max, 'ptMax': pt_max, 'tsMax': ts_max,
        'maxTotal': reg_max + pt_max + ts_max,
        'groupedReg': grouped_reg, 'groupedPt': grouped_pt, 'tsSessions': ts_sessions,
        'regUsed': reg_used, 'ptUsed': pt_used, 'tsUsed': ts_used,
        'used': round(reg_used + pt_used + ts_used, 2),
    }


def has_capacity(committed_hours, additional_hours, reg_max, pt_max, ts_max, tol=1e-9):
    """Exact-boundary allowed: reaching exactly a cap is valid, only exceeding it isn't."""
    return committed_hours + additional_hours <= (reg_max + pt_max + ts_max) + tol
