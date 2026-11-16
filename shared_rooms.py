"""
Shared rooms — Admin Settings > Merging & Calendar > Shared Rooms.

A shared room (e.g. a gym, a quadrangle, a big hall) may hold several classes AT THE
SAME TIME — different sections, subjects and faculty — when they have exactly the same
start and end time, up to its limit of SECTIONS (a merged class of three sections counts
as three). Each exact time is counted on its own: e.g. three sections at 7:30–9:30, then
1:00–3:00, 3:00–5:00 and 6:00–9:00 on the same day are all fine. A class that only PARTLY
overlaps another one in the room (3:00–6:00 vs 3:30–6:00) is still a room conflict —
EXCEPT between two classes of subjects with the same merge setting (Settings → Class
Merging, e.g. the NSTP rule covers every NSTP/OU code): those may overlap in any way, as
long as the room never holds more than its limit of sections at any moment. Rooms without
a policy keep HC11 as it is (one independent class at a time, plus valid HC16 merges).

Used by every room check — Schedule Generation, CSPValidator (HC11), the Manual Editor,
Save Draft / Publish, the Local Scheduler and Make-up / Schedule Adjustment requests:

    limits() / limit_for(roomid)       {roomid(str): max_sections} (cached) / one room's limit
    flex_rules() / flex_tag(code)      Class Merging rule codes (cached) / the rule covering a subject
    check_join(existing, new, limit)   may `new` join the room? (time rule, seats left)
    violations(intervals, limit)       what is wrong with a set of classes in one room/day
    full_spans(intervals, limit)       the class times that are full (for "blocked time" lists)
    generator_slots(slots, limit) / blocking(slots, start, end)
                                       Schedule Generation's booked-slot lists (exact times only)

An interval is (start, end[, section_key[, flex_tag]]); start/end are anything comparable
(times, timeslot ids, minutes), end exclusive, so 10:30-12:00 and 12:00-1:30 never overlap.
Without a section_key each interval counts as one section; with one, a section is counted
once however many of its rows are in the room at that moment. Two intervals with the same
non-empty flex_tag (flex_tag(subject code)) may partly overlap.
"""
import threading
import time

TABLE_DDL = [
    """CREATE TABLE IF NOT EXISTS public.shared_room_policy (
        roomid       INTEGER PRIMARY KEY REFERENCES public.room(roomid) ON DELETE CASCADE,
        max_sections SMALLINT NOT NULL CHECK (max_sections BETWEEN 2 AND 50),
        updated_by   VARCHAR(100),
        updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
]

_CACHE_SECONDS = 15
_cache = {'at': 0.0, 'limits': {}}
_flex_cache = {'at': 0.0, 'codes': []}
_lock = threading.Lock()
_ensured = False


def ensure_table(cur):
    global _ensured
    if _ensured:
        return
    for sql in TABLE_DDL:
        cur.execute(sql)
    _ensured = True


def load_limits(cur):
    """{roomid(str): max_sections} read with the caller's cursor (dict or tuple rows)."""
    ensure_table(cur)
    cur.execute("SELECT roomid, max_sections FROM public.shared_room_policy")
    out = {}
    for r in cur.fetchall() or []:
        rid, n = (r['roomid'], r['max_sections']) if isinstance(r, dict) else (r[0], r[1])
        out[str(rid)] = int(n)
    return out


def invalidate():
    with _lock:
        _cache['at'] = 0.0
        _flex_cache['at'] = 0.0


def limits():
    """Cached {roomid(str): max_sections}. Never raises: {} if the database can't be read."""
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


def limit_for(roomid, table=None):
    if roomid is None or str(roomid).strip().upper() in ('', 'TBA', 'NONE'):
        return None
    return (table if table is not None else limits()).get(str(roomid).strip())


# ── Subjects with a merge setting (Settings → Class Merging) may partly overlap ──

def _first(row):
    return list(row.values())[0] if isinstance(row, dict) else row[0]


def flex_rules():
    """Cached subject codes of the Class Merging rules (merge_load_policy), e.g. ['NSTP'].
    Never raises: [] if the table is missing or can't be read."""
    now = time.time()
    with _lock:
        if now - _flex_cache['at'] < _CACHE_SECONDS:
            return list(_flex_cache['codes'])
    try:
        from database import get_db_connection
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT to_regclass('public.merge_load_policy') IS NOT NULL")
            codes = []
            if _first(cur.fetchone()):
                cur.execute("SELECT DISTINCT UPPER(TRIM(subjectcode)) FROM public.merge_load_policy")
                codes = sorted({_first(x) or '' for x in cur.fetchall() or []} - {''})
            conn.commit()
        finally:
            conn.close()
    except Exception:
        codes = None
    with _lock:
        if codes is not None:
            _flex_cache['codes'], _flex_cache['at'] = codes, now
        return list(_flex_cache['codes'])


def _code(code):
    return ' '.join(str(code or '').upper().split())


def flex_tag(subjectcode, rules=None):
    """The Class Merging rule covering `subjectcode` ('NSTP' covers every NSTP/OU code —
    the same matching as merge_groups.merge_section_limit), or None."""
    code = _code(subjectcode)
    if not code:
        return None
    for rc in (rules if rules is not None else flex_rules()):
        rc = _code(rc)
        if rc == code or (rc == 'NSTP' and code.startswith(('NSTP', 'OU'))):
            return rc
    return None


def _norm(iv):
    s, e = iv[0], iv[1]
    key = iv[2] if len(iv) > 2 else None
    tag = iv[3] if len(iv) > 3 else None
    return s, e, key, tag


def _overlaps(a, b):
    return a[0] < b[1] and a[1] > b[0]


def _may_overlap(a, b):
    """Two overlapping classes may share the room: at exactly the same time, or — at any
    times — when both are subjects of the same Class Merging rule (e.g. two NSTP classes)."""
    return (a[0], a[1]) == (b[0], b[1]) or (a[3] is not None and a[3] == b[3])


def _keyed(ivs):
    return [(s, e, k if k is not None else ('#', i), t) for i, (s, e, k, t) in enumerate(ivs)]


def _valid(ivs):
    return [iv for iv in ivs if iv[0] is not None and iv[1] is not None]


def _peak(ivs, s, e):
    """Most sections in the room at any moment of [s, e)."""
    points = sorted({s} | {x[0] for x in ivs if s < x[0] < e})
    return max((len({x[2] for x in ivs if x[0] <= p < x[1]}) for p in points), default=0)


def check_join(existing, new, limit):
    """May the classes in `new` join a shared room already holding `existing`?
    {'ok': True} or {'ok': False, 'reason': 'partial'|'full', 'with': (start, end),
     'count': most sections at once incl. the new ones, 'limit': limit}."""
    ex_raw, nw_raw = _valid(existing), _valid(new)
    ivs = _keyed([_norm(iv) for iv in ex_raw + nw_raw])
    ex, nw = ivs[:len(ex_raw)], ivs[len(ex_raw):]
    for a in nw:
        for b in ex:
            if _overlaps(a, b) and not _may_overlap(a, b):
                return {'ok': False, 'reason': 'partial', 'with': (b[0], b[1]), 'count': None, 'limit': limit}
    for a in nw:
        n = _peak(ivs, a[0], a[1])
        if n > limit:
            return {'ok': False, 'reason': 'full', 'with': (a[0], a[1]), 'count': n, 'limit': limit}
    return {'ok': True}


def violations(intervals, limit):
    """[{'reason': 'partial'|'full', 'with': (s, e), 'other': (s, e)|None, 'count': n}]
    for a set of classes in one shared room on one day."""
    ivs = _keyed([_norm(iv) for iv in _valid(intervals)])
    out, seen = [], set()
    for i, a in enumerate(ivs):
        for b in ivs[i + 1:]:
            pair = tuple(sorted([(a[0], a[1]), (b[0], b[1])]))
            if _overlaps(a, b) and not _may_overlap(a, b) and pair not in seen:
                seen.add(pair)
                out.append({'reason': 'partial', 'with': pair[0], 'other': pair[1], 'count': None})
    for t in sorted({(x[0], x[1]) for x in ivs}):
        n = _peak(ivs, *t)
        if n > limit:
            out.append({'reason': 'full', 'with': t, 'other': None, 'count': n})
    return out


def full_spans(intervals, limit):
    """[(start, end)] of the class times during which the room already holds `limit` sections."""
    ivs = _keyed([_norm(iv) for iv in _valid(intervals)])
    return sorted(t for t in {(x[0], x[1]) for x in ivs} if _peak(ivs, *t) >= limit)


class JoinableSlot(tuple):
    """A booked (start, end) in a shared room that still has seats: a new class may use
    the room at EXACTLY this time (any other overlap is still blocked)."""
    __slots__ = ()


def generator_slots(slots, limit):
    """Schedule Generation's booked (start, end) list for one shared room/day: one entry
    per exact time, a JoinableSlot while that time has seats left (each listed slot is
    one section's class)."""
    counts = {}
    for sl in slots:
        key = (sl[0], sl[1])
        counts[key] = counts.get(key, 0) + 1
    return [JoinableSlot(k) if n < limit else k for k, n in counts.items()]


def blocking(slots, start, end):
    """The booked slots that really block a class at start–end: a JoinableSlot doesn't
    when the class is at exactly its time."""
    return [sl for sl in slots
            if not (isinstance(sl, JoinableSlot) and sl[0] == start and sl[1] == end)]
