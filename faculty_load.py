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

# Live query: for a single faculty, every currently-active scheduled time slice
# (Draft-preferred per subject+section, per the BOOL_OR status-group rule — see
# project_faculty_load_calc memory) with its REAL elapsed hours computed from the
# actual assigned timeslots. This is the only true "actual scheduled hours" source
# in the codebase; every load computation must ultimately run through this.
FACULTY_SESSIONS_SQL = """
    WITH scoped AS (
        SELECT sv.versionid, sv.status,
               BOOL_OR(sv.status = 'Draft') OVER (
                   PARTITION BY cs.subjectcode, sc.sectionid
               ) AS has_draft
        FROM schedule_version sv
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN semester sem ON sc.semesterid = sem.semesterid
        WHERE sc.employeenumber = %s
          AND sem.academicyearid = %s
          AND sem.semestertype   = %s
          AND sv.status IN ('Published','Draft')
    ),
    ranked AS (
        SELECT versionid
        FROM scoped
        WHERE (has_draft AND status = 'Draft') OR (NOT has_draft AND status = 'Published')
    )
    SELECT
        cs.subjectcode,
        cs.subjectname,
        COALESCE(cs.creditunits,0) AS units,
        ROUND(EXTRACT(EPOCH FROM (ts_e.timevalue - ts_s.timevalue)) / 3600.0, 2) AS hrs,
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
        END AS year_section,
        sec.sectionid,
        TO_CHAR(ts_s.timevalue,'HH12:MI AM') || ' - ' || TO_CHAR(ts_e.timevalue,'HH12:MI AM') AS time_range,
        LPAD(EXTRACT(HOUR FROM ts_s.timevalue)::text,2,'0') ||
        LPAD(EXTRACT(HOUR FROM ts_e.timevalue)::text,2,'0') AS time_code,
        ss.daydesc AS days,
        COALESCE(r.roomname,'—') AS room,
        sv.status
    FROM ranked rk
    JOIN schedule_sessions ss ON ss.versionid = rk.versionid
    JOIN schedule_version sv ON sv.versionid = rk.versionid
    JOIN schedule sc ON sv.scheduleid = sc.scheduleid
    JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
    JOIN semester sem ON sc.semesterid = sem.semesterid
    LEFT JOIN sections sec ON sc.sectionid = sec.sectionid
    LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
    LEFT JOIN room r ON ss.roomid = r.roomid
    LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
    LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
    ORDER BY cs.subjectcode, ts_s.timevalue
"""

# Batch variant: total real scheduled hours per faculty, for a whole set of faculty
# at once (used where a per-faculty round trip would be too slow — DSS suggest,
# cross-program overload checks). Same Draft-preferred status-group resolution.
_BATCH_HOURS_SQL_TMPL = """
    WITH scoped AS (
        SELECT sv.versionid, sv.status, sc.employeenumber,
               BOOL_OR(sv.status = 'Draft') OVER (
                   PARTITION BY sc.employeenumber, cs.subjectcode, sc.sectionid
               ) AS has_draft
        FROM schedule_version sv
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON sc.curriculumsubjectid = cs.curriculumsubjectid
        JOIN semester sem ON sc.semesterid = sem.semesterid
        JOIN curriculum c ON cs.curriculumid = c.curriculumid
        WHERE sem.academicyearid = %(ay_id)s AND sem.semestertype = %(sem)s
          AND sv.status IN ('Published','Draft')
          {extra_where}
    ),
    ranked AS (
        SELECT DISTINCT versionid, employeenumber
        FROM scoped
        WHERE (has_draft AND status = 'Draft') OR (NOT has_draft AND status = 'Published')
    )
    SELECT rk.employeenumber,
           COALESCE(SUM(ROUND(EXTRACT(EPOCH FROM (ts_e.timevalue - ts_s.timevalue)) / 3600.0, 2)), 0) AS hrs
    FROM ranked rk
    JOIN schedule_sessions ss ON ss.versionid = rk.versionid
    LEFT JOIN timeslot ts_s ON ss.starttimeid = ts_s.timeid
    LEFT JOIN timeslot ts_e ON ss.endtimeid = ts_e.timeid
    GROUP BY rk.employeenumber
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


_FACULTY_SESSIONS_PUBLISHED_ONLY_SQL = FACULTY_SESSIONS_SQL.replace(
    "AND sv.status IN ('Published','Draft')", "AND sv.status = 'Published'"
).replace(
    "WHERE (has_draft AND status = 'Draft') OR (NOT has_draft AND status = 'Published')",
    "WHERE status = 'Published'"
)


def get_faculty_sessions(cur, emp_num, ay_id, sem, published_only=False):
    sql = _FACULTY_SESSIONS_PUBLISHED_ONLY_SQL if published_only else FACULTY_SESSIONS_SQL
    cur.execute(sql, (emp_num, ay_id, sem))
    return [dict(r) for r in (cur.fetchall() or [])]


def get_faculty_scheduled_hours(cur, emp_num, ay_id, sem, published_only=False):
    """Total real scheduled hours currently committed for one faculty, plus the raw
    session rows (callers that only need the total can ignore the second value).
    `published_only=True` mirrors the old scheduler_mode=='local' behavior (Published
    rows only, no Draft-preferred resolution)."""
    sessions = get_faculty_sessions(cur, emp_num, ay_id, sem, published_only=published_only)
    return round(sum(float(s.get('hrs') or 0) for s in sessions), 2), sessions


def get_faculty_hours_batch(cur, ay_id, sem, exclude_program=None, exclude_year_level=None):
    """Total real scheduled hours per faculty (dict: employeenumber -> hours) across ALL
    faculty for the given AY/semester in one query. Pass exclude_program/exclude_year_level
    to omit that program+year's own rows (used by cross-program overload checks that need
    "everything EXCEPT the section currently being saved")."""
    extra_where = ''
    params = {'ay_id': ay_id, 'sem': sem}
    if exclude_program and exclude_year_level is not None:
        extra_where = 'AND NOT (UPPER(c.programcode) = %(excl_prog)s AND cs.yearlevel = %(excl_yl)s)'
        params['excl_prog'] = exclude_program.upper()
        params['excl_yl'] = int(exclude_year_level)
    sql = _BATCH_HOURS_SQL_TMPL.format(extra_where=extra_where)
    cur.execute(sql, params)
    return {r['employeenumber']: float(r['hrs'] or 0) for r in (cur.fetchall() or [])}


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
    from database import parse_merge_scope_subjects, code_in_merge_scope
    cfg = config or {}
    if not bool(cfg.get('hc_merge_enabled', 1)):
        return False
    code_u = (subject_code or '').upper()
    is_nstp = code_u.startswith(('NSTP', 'OU'))
    scope_subjects = parse_merge_scope_subjects(cfg.get('hc_merge_scope_subjects'))
    if scope_subjects is not None:
        return code_in_merge_scope(code_u, scope_subjects)
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


def group_assignments(sessions, config=None):
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
    """
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
            first = idxs[0]
            first_label = str(sessions[first].get('year_section') or '').upper() or None
            for other in idxs[1:]:
                other_section = sessions[other].get('year_section')
                if other_section == sessions[first].get('year_section'):
                    continue  # identical row for the SAME section -- not a merge case
                other_label = str(other_section or '').upper() or None
                if _section_pair_allowed_by_label(first_label, other_label, merge_section_pairs):
                    skip_hours[other] = True

    groups = {}
    order = []
    for i, s in enumerate(sessions):
        key = (s.get('subjectcode'), s.get('year_section'))
        h = 0.0 if skip_hours[i] else float(s.get('hrs') or 0)
        reg = is_reg_slice(s.get('days'), s.get('time_code'))
        if key not in groups:
            g = dict(s)
            g['_reg_hrs'] = h if reg else 0.0
            g['_pt_hrs'] = 0.0 if reg else h
            g['_days'] = [s.get('days')] if s.get('days') else []
            g['_times'] = [s.get('time_range')] if s.get('time_range') else []
            pair = (s.get('days'), s.get('time_range'))
            g['_reg_pairs'] = [pair] if (reg and s.get('days')) else []
            g['_pt_pairs'] = [pair] if (not reg and s.get('days')) else []
            g['hrs'] = h
            groups[key] = g
            order.append(key)
        else:
            g = groups[key]
            g['hrs'] += h
            if reg:
                g['_reg_hrs'] += h
            else:
                g['_pt_hrs'] += h
            if s.get('days'):
                g['_days'].append(s.get('days'))
                g['_times'].append(s.get('time_range'))
                pair = (s.get('days'), s.get('time_range'))
                (g['_reg_pairs'] if reg else g['_pt_pairs']).append(pair)

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

    if is_part_time:
        # Part-time faculty have no separate daytime-duty bucket — every session is PT
        # first; only overflow beyond ptMax spills into TS.
        grouped = group_assignments(sessions, config=config)
        pt_kept, pt_spilled = cap_spill(grouped, pt_max)
        grouped_reg, grouped_pt, ts_sessions = [], pt_kept, pt_spilled
    else:
        grouped = group_assignments(sessions, config=config)
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
        'regMax': reg_max, 'ptMax': pt_max, 'tsMax': ts_max,
        'maxTotal': reg_max + pt_max + ts_max,
        'groupedReg': grouped_reg, 'groupedPt': grouped_pt, 'tsSessions': ts_sessions,
        'regUsed': reg_used, 'ptUsed': pt_used, 'tsUsed': ts_used,
        'used': round(reg_used + pt_used + ts_used, 2),
    }


def has_capacity(committed_hours, additional_hours, reg_max, pt_max, ts_max, tol=1e-9):
    """Exact-boundary allowed: reaching exactly a cap is valid, only exceeding it isn't."""
    return committed_hours + additional_hours <= (reg_max + pt_max + ts_max) + tol
