"""Shared faculty teaching-load computation.

Single source of truth for Regular / Part-Time / Teaching-Substitution load caps
and usage. Load is measured in ACTUAL SCHEDULED HOURS (real timeslot start/end),
never curriculum credit units. Used by both app.py (Manual Editor, Faculty Load
tab, DSS suggestions, validation) and scheduler.py (the GA) so they can't drift
from each other the way the old per-file duplicate implementations did.

`employeetype.regularload` / `parttimeload` / `teachingsubstitution` and
`designation.regularloadunit` are reinterpreted IN PLACE as hour caps (no schema
change) — the same columns that used to mean "credit units" now mean "hours".
"""

WEEKDAYS = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'}

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
        COALESCE(pyl.programcode,'') || '-' || COALESCE(pyl.yearlevel::text,'')
            || ' ' || COALESCE(sec.sectionname,'') AS year_section,
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


def classify_slice(day, end_hour):
    """Regular = weekday, ends at/before 4 PM. Everything else (evenings, Sat/Sun) is PT."""
    try:
        end_hour = int(end_hour)
    except (TypeError, ValueError):
        end_hour = 0
    return 'regular' if (day in WEEKDAYS and end_hour <= 16) else 'pt'


def _time_code_end_hour(time_code):
    try:
        return int(str(time_code or '')[2:])
    except ValueError:
        return 0


def is_reg_slice(days, time_code):
    return classify_slice(days, _time_code_end_hour(time_code)) == 'regular'


def get_faculty_caps(faculty_row):
    """faculty_row needs: designationid, plus Regular/PT/TS caps and a type label under any
    of the several key-name conventions the existing call sites already use (regularload/
    reg_load, parttimeload/pt_load, teachingsubstitution/teach_sub, designation_regular_load/
    desig_reg_load, typename/employee_type/employeestatus). Returns (reg_max, pt_max, ts_max)
    in HOURS — same lookup rule as before (designation overrides Regular only; PT/TS always
    come from the faculty's own employeetype; a designation's nightteachingservice is a
    separate "nights on duty" cap, never load)."""
    g = faculty_row.get
    has_desig = g('designationid') is not None
    typename = (g('typename') or g('employee_type') or g('employeestatus') or '').lower()
    pt_max = float(g('parttimeload') or g('pt_load') or 0)
    ts_max = float(g('teachingsubstitution') or g('teach_sub') or 0)
    if has_desig:
        reg_max = float(g('designation_regular_load') or g('desig_reg_load') or 0)
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


def group_assignments(sessions):
    """Merge multi-slice rows of the same (subjectcode, year_section) into one assignment,
    tracking hour totals split by Regular-time vs PT-time slices. Mirrors the JS
    `_flGroupSessions` algorithm this module replaces so nothing can diverge again.
    Each input session dict needs: subjectcode, year_section, days, time_code, time_range, hrs.
    """
    groups = {}
    order = []
    for s in sessions:
        key = (s.get('subjectcode'), s.get('year_section'))
        h = float(s.get('hrs') or 0)
        reg = is_reg_slice(s.get('days'), s.get('time_code'))
        if key not in groups:
            g = dict(s)
            g['_reg_hrs'] = h if reg else 0.0
            g['_pt_hrs'] = 0.0 if reg else h
            g['_days'] = [s.get('days')] if s.get('days') else []
            g['_times'] = [s.get('time_range')] if s.get('time_range') else []
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
    result = []
    for key in order:
        g = groups[key]
        g['days'] = ', '.join(g['_days'])
        g['time_range'] = ', '.join(g['_times'])
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


def compute_load_buckets(sessions, faculty_row):
    """Given a faculty's raw per-slice session rows (already merge-collapsed by the caller
    if Class Merging applies) and their cap-lookup row, return Regular/PT/TS hour usage.
    This is the single source of truth reused by the Faculty Load tab, the assignment-time
    validation gate, the Reassign side panel, DSS "suggest faculty", and the GA's post-hoc
    HC8 check — replaces the old JS-only `_computeFacultyLoadBuckets`."""
    reg_max, pt_max, ts_max = get_faculty_caps(faculty_row)
    g = faculty_row.get
    typename = (g('typename') or g('employee_type') or g('employeestatus') or '').lower()
    is_part_time = 'part' in typename and g('designationid') is None

    if is_part_time:
        # Part-time faculty have no separate daytime-duty bucket — every session is PT
        # first; only overflow beyond ptMax spills into TS.
        grouped = group_assignments(sessions)
        pt_kept, pt_spilled = cap_spill(grouped, pt_max)
        grouped_reg, grouped_pt, ts_sessions = [], pt_kept, pt_spilled
    else:
        grouped = group_assignments(sessions)
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
                p = dict(g); p['hrs'] = round(g['_pt_hrs'], 2); p['_split'] = True
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
