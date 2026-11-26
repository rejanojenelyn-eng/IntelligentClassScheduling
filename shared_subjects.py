"""
Shared subjects — Admin Settings > Merging & Calendar > Shared Subjects.

A shared subject (e.g. a lecture taught by one faculty member and its laboratory by
another) may be split between up to `max_faculty` faculty members per section. The
Academic Head chooses the faculty PER SLICE in the Manual Editor: each slice (one
day/time/room meeting) belongs to exactly one of them, and the calendar labels it
"SLICE 1 · REYES".

Storage: schedule_sessions.employeenumber is the slice's own faculty. NULL means "the
subject's faculty" (schedule_version.employeenumber, then schedule.employeenumber), so
every subject that is not shared — and every row written before this feature — reads
exactly as before. Only subjects listed here ever get a non-NULL slice faculty.

Load: each faculty member is credited the HOURS of their own slices; the subject's
credit units are split in the same ratio (unit_share_sql).

    limits() / limit_for(code)    {SUBJECTCODE: max_faculty} (cached) / one subject's limit
    fac_sql(ss, sv, sc)           SQL: the faculty of one session row
    unit_share_sql(vid, fac)      SQL: the share (0-1] of the subject's units for that faculty
    slice_faculty(code, fac, version_fac, table)
                                  what to store in schedule_sessions.employeenumber
"""
import threading
import time

MAX_LIMIT = 10

TABLE_DDL = [
    """CREATE TABLE IF NOT EXISTS public.shared_subject_policy (
        subjectcode  VARCHAR(30) PRIMARY KEY,
        max_faculty  SMALLINT NOT NULL CHECK (max_faculty BETWEEN 2 AND 10),
        updated_by   VARCHAR(100),
        updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    # The slice's own faculty (NULL = the subject's faculty). ON DELETE SET NULL: a
    # removed faculty row falls back to the subject's faculty instead of blocking.
    """ALTER TABLE public.schedule_sessions
        ADD COLUMN IF NOT EXISTS employeenumber VARCHAR(30)
        REFERENCES public.faculty(employeenumber) ON DELETE SET NULL""",
    "CREATE INDEX IF NOT EXISTS idx_session_faculty ON public.schedule_sessions (employeenumber)",
]

_CACHE_SECONDS = 15
_cache = {'at': 0.0, 'limits': {}}
_lock = threading.Lock()
_ensured = False


def norm_code(code):
    return ' '.join(str(code or '').upper().split())


def ensure_table(cur):
    global _ensured
    if _ensured:
        return
    for sql in TABLE_DDL:
        cur.execute(sql)
    _ensured = True


def load_limits(cur):
    """{SUBJECTCODE: max_faculty} read with the caller's cursor (dict or tuple rows)."""
    ensure_table(cur)
    cur.execute("SELECT subjectcode, max_faculty FROM public.shared_subject_policy")
    out = {}
    for r in cur.fetchall() or []:
        code, n = (r['subjectcode'], r['max_faculty']) if isinstance(r, dict) else (r[0], r[1])
        out[norm_code(code)] = int(n)
    return out


def invalidate():
    with _lock:
        _cache['at'] = 0.0


def limits():
    """Cached {SUBJECTCODE: max_faculty}. Never raises: {} if the database can't be read."""
    now = time.time()
    with _lock:
        if now - _cache['at'] < _CACHE_SECONDS:
            return dict(_cache['limits'])
    try:
        from database import get_db_connection
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            data = load_limits(cur)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        data = None
    with _lock:
        if data is not None:
            _cache['limits'], _cache['at'] = data, now
        return dict(_cache['limits'])


def limit_for(subjectcode, table=None):
    """Most faculty who may share `subjectcode` in one section, or None (not shared)."""
    code = norm_code(subjectcode)
    if not code:
        return None
    return (table if table is not None else limits()).get(code)


def _blank(fac):
    return str(fac or '').strip().upper() in ('', 'TBA', 'NONE', 'NULL')


def slice_faculty(subjectcode, fac, table=None):
    """Value for schedule_sessions.employeenumber: the slice's faculty for a shared
    subject, else None (the slice follows the subject's faculty as before)."""
    if _blank(fac) or not limit_for(subjectcode, table):
        return None
    return str(fac).strip()


def fac_sql(ss='ss', sv='sv', sc='sc'):
    """SQL expression: the faculty teaching one session row."""
    return f"COALESCE({ss}.employeenumber, {sv}.employeenumber, {sc}.employeenumber)"


def unit_share_sql(versionid_expr, faculty_expr):
    """SQL expression: the part (0-1] of the subject's credit units credited to
    `faculty_expr` for the schedule_version `versionid_expr` — 1 for an ordinary subject
    (no slice has its own faculty); for a shared subject, that faculty's hours in the
    version divided by all of the version's hours."""
    hrs = "EXTRACT(EPOCH FROM (u_te.timevalue - u_ts.timevalue))"
    return f"""COALESCE((
        SELECT CASE WHEN COUNT(u_ss.employeenumber) = 0 OR SUM({hrs}) <= 0 THEN 1
                    ELSE SUM(CASE WHEN COALESCE(u_ss.employeenumber, u_sv.employeenumber, u_sc.employeenumber)
                                       IS NOT DISTINCT FROM ({faculty_expr}) THEN {hrs} ELSE 0 END) / SUM({hrs})
               END
        FROM public.schedule_sessions u_ss
        JOIN public.schedule_version u_sv ON u_sv.versionid = u_ss.versionid
        JOIN public.schedule u_sc ON u_sc.scheduleid = u_sv.scheduleid
        JOIN public.timeslot u_ts ON u_ts.timeid = u_ss.starttimeid
        JOIN public.timeslot u_te ON u_te.timeid = u_ss.endtimeid
        WHERE u_ss.versionid = ({versionid_expr})), 1)"""


def check_faculty_count(rows, table=None):
    """Shared subjects whose rows use more distinct faculty in one section than allowed:
    [{'subjectcode', 'section', 'count', 'limit'}]. `rows`: dicts with subject_code/
    subjectcode, faculty_id/employeenumber and (optionally) section_id/sectionid."""
    table = table if table is not None else limits()
    by_key = {}
    for r in rows or []:
        code = norm_code(r.get('subjectcode') or r.get('subject_code'))
        fac = r.get('employeenumber') or r.get('faculty_id')
        if code and code in table and not _blank(fac):
            sec = str(r.get('section_id') or r.get('sectionid') or '')
            by_key.setdefault((code, sec), set()).add(str(fac).strip())
    return [{'subjectcode': code, 'section': sec, 'count': len(facs), 'limit': table[code]}
            for (code, sec), facs in sorted(by_key.items()) if len(facs) > table[code]]


def main_faculty(rows, table=None):
    """{SUBJECTCODE: faculty} for the shared subjects among `rows` (one section's save):
    the faculty with the most slices (meeting days), first seen on a tie. Rows: dicts with
    subjectcode/subject_code, employeenumber/faculty_id and days_list/daydesc/day."""
    table = table if table is not None else limits()
    counts, order = {}, {}
    for r in rows or []:
        code = norm_code(r.get('subjectcode') or r.get('subject_code'))
        fac = r.get('employeenumber') or r.get('faculty_id')
        if not code or code not in table or _blank(fac):
            continue
        fac = str(fac).strip()
        n = len(r.get('days_list') or []) or 1
        c = counts.setdefault(code, {})
        c[fac] = c.get(fac, 0) + n
        seen = order.setdefault(code, [])
        if fac not in seen:
            seen.append(fac)
    return {code: max(c, key=lambda f: (c[f], -order[code].index(f))) for code, c in counts.items()}


def teaches_version_sql(sv, sc, me):
    """SQL condition: faculty `me` (an SQL expression) teaches the schedule_version `sv` —
    as the subject's faculty, or as the faculty of at least one of its slices."""
    return (f"({sc}.employeenumber = {me} OR EXISTS (SELECT 1 FROM public.schedule_sessions t_ss "
            f"WHERE t_ss.versionid = {sv}.versionid AND t_ss.employeenumber = {me}))")
