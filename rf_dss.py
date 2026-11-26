import re as _re
import time as _time

from database import get_db_connection
from psycopg2.extras import RealDictCursor

try:
    from sklearn.ensemble import RandomForestRegressor as _RFR
    import numpy as _np
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_sc(code: str) -> str:
    """'CS 101', 'CS-101', 'CS_101' → 'CS101'."""
    return _re.sub(r'[^A-Z0-9]', '', code.upper())


def _ay_to_int(ay_id: str) -> int:
    """'AY2425' → 2425."""
    digits = _re.sub(r'[^0-9]', '', ay_id or '')
    return int(digits) if digits else 0


def _ay_start_year(ay_id: str) -> int:
    """'AY2425' → 2024 (start calendar year)."""
    digits = _re.sub(r'[^0-9]', '', ay_id or '')
    if len(digits) >= 4:
        try:
            return int('20' + digits[:2])
        except ValueError:
            pass
    return 0


def _parse_inst_name(raw: str):
    """
    Parse a historical instructor name into (last_lower, first_token_lower).
    Handles two formats:
      'SANTOS, JOSE B'  →  ('santos', 'jose')
      'SANTOS, J.'      →  ('santos', 'j')
      'JOSE SANTOS'     →  ('santos', 'jose')
    Returns ('', '') when the name cannot be parsed.
    first_token may be a full name or a single initial — callers must handle both.
    """
    s = raw.strip().lower()
    if not s:
        return ('', '')
    if ',' in s:
        parts = s.split(',', 1)
        last  = parts[0].strip()
        rest  = parts[1].strip()
        fi    = rest.split()[0].rstrip('.') if rest else ''
    else:
        tokens = s.split()
        if len(tokens) == 1:
            return (tokens[0], '')
        # 'FIRST [MID...] LAST' — last token is the surname
        last = tokens[-1]
        fi   = tokens[0].rstrip('.')
    return (last, fi)


def _fac_first_match(fac_fn, hist_fi):
    """Bidirectional first-name/initial match, e.g. "JOSE"~"JOSEPH",
    "MA."~"MARIA". fac_fn/hist_fi are already lowercased, stripped tokens."""
    if not fac_fn or not hist_fi:
        return False
    if len(hist_fi) > 1 and len(fac_fn) > 1:
        return hist_fi == fac_fn or fac_fn.startswith(hist_fi) or hist_fi.startswith(fac_fn)
    return hist_fi[0] == fac_fn[0]


def _match_one_name(raw_name, by_last):
    """Resolve one raw instructor-name string against `by_last` (a dict of
    lowercase last_name -> list of faculty rows, each carrying at least
    'empnum', 'first_name'). Shared core used by both _build_name_to_empnum
    (RF training, historical_data-driven) and resolve_faculty_names
    (Historical Data Import, file-driven) so the two never disagree.

    Returns (status, empnum, candidates):
      'matched'       - empnum set, candidates=[the matched faculty row]
      'ambiguous'     - empnum=None, candidates=the plausible faculty rows
      'no_candidates' - empnum=None, candidates=[] (last name parsed fine,
                        but no Faculty record shares it)
      'unparseable'   - empnum=None, candidates=[] (raw_name had no usable
                        last name at all, e.g. blank)
    """
    last, fi = _parse_inst_name(raw_name)
    if not last:
        return ('unparseable', None, [])

    candidates = by_last.get(last, [])
    if not candidates:
        return ('no_candidates', None, [])

    if len(candidates) == 1:
        fac = candidates[0]
        if fi and fac['first_name'] and not _fac_first_match(fi, fac['first_name']):
            return ('ambiguous', None, [fac])
        return ('matched', fac['empnum'], [fac])

    # Multiple faculty share this surname — use first name/initial to pick one.
    if not fi:
        return ('ambiguous', None, candidates)
    matches = [f for f in candidates if _fac_first_match(fi, f['first_name'])]
    if len(matches) == 1:
        return ('matched', matches[0]['empnum'], matches)
    return ('ambiguous', None, matches or candidates)


def _faculty_by_last_name(cur):
    """Fetch active faculty grouped by lowercase last name, for _match_one_name."""
    cur.execute("""
        SELECT CAST(EmployeeNumber AS TEXT)          AS empnum,
               LOWER(TRIM(LastName))                AS last_name,
               LOWER(TRIM(COALESCE(FirstName, ''))) AS first_name,
               TRIM(LastName)                        AS last_name_disp,
               TRIM(COALESCE(FirstName, ''))         AS first_name_disp
        FROM Faculty
        WHERE EmployeeStatus != 'Archived'
    """)
    faculty = cur.fetchall() or []
    by_last = {}
    for f in faculty:
        by_last.setdefault(f['last_name'], []).append(f)
    return by_last


def _build_name_to_empnum(cur):
    """
    Map UPPER(historical_data."Instructor") → str(employee_number).

    Resolution rules:
      1. Extract (last_name, first_initial) from the historical name.
      2. Find Faculty rows with the same last name.
      3. Unique last-name match → resolved.
      4. Multiple same-surname faculty → require matching first initial.
      5. Still multiple → ambiguous; excluded from the mapping.
      6. No Faculty match at all → also excluded.

    Returns (name_to_empnum: dict, ambiguous_names: set).
    """
    by_last = _faculty_by_last_name(cur)

    cur.execute("""
        SELECT DISTINCT UPPER(TRIM("Instructor")) AS inst_key,
                        TRIM("Instructor")        AS inst_raw,
                        MAX(employeenumber)        AS employeenumber
        FROM historical_data
        WHERE "Instructor" IS NOT NULL AND TRIM("Instructor") != ''
        GROUP BY UPPER(TRIM("Instructor")), TRIM("Instructor")
    """)
    rows = cur.fetchall() or []

    name_to_empnum = {}
    ambiguous      = set()

    for row in rows:
        key = row['inst_key']
        # If the stored employee number is already in historical_data, use it directly.
        if row.get('employeenumber'):
            name_to_empnum[key] = str(row['employeenumber'])
            continue

        status, empnum, _cands = _match_one_name(row['inst_raw'], by_last)
        if status == 'matched':
            name_to_empnum[key] = empnum
        elif status in ('ambiguous', 'unparseable'):
            ambiguous.add(key)
        # 'no_candidates' -> excluded from both, same as the original code's
        # bare `continue` (no Faculty record shares that surname at all).

    return name_to_empnum, ambiguous


def resolve_faculty_names(cur, raw_names):
    """Resolve a batch of raw instructor-name strings (as they appear in an
    uploaded schedule file) against the faculty table, for the Historical
    Data Import feature. One DB round-trip regardless of how many names are
    passed in.

    Returns {raw_name: {'status', 'employeenumber', 'name', 'candidates'}}
      'matched'   - resolved unambiguously; employeenumber/name are set.
      'ambiguous' - more than one plausible Faculty record; 'candidates' is
                    [{'employeenumber','name'}, ...] for the caller to ask
                    the user to pick from (or add a new Faculty instead).
      'new'       - no existing Faculty record plausibly matches (including
                    an unparseable/blank name) — caller should offer to
                    create one.
    """
    by_last = _faculty_by_last_name(cur)

    def _disp(fac):
        last  = (fac.get('last_name_disp')  or '').strip()
        first = (fac.get('first_name_disp') or '').strip()
        name  = f"{last}, {first}".strip(', ') if (last or first) else fac['empnum']
        return {'employeenumber': fac['empnum'], 'name': name}

    out = {}
    for raw_name in raw_names or []:
        status, empnum, candidates = _match_one_name(raw_name, by_last)
        if status == 'matched':
            fac = candidates[0] if candidates else None
            out[raw_name] = {
                'status': 'matched',
                'employeenumber': empnum,
                'name': _disp(fac)['name'] if fac else None,
                'candidates': [],
            }
        elif status == 'ambiguous':
            out[raw_name] = {
                'status': 'ambiguous', 'employeenumber': None, 'name': None,
                'candidates': [_disp(f) for f in candidates],
            }
        else:  # 'no_candidates' or 'unparseable'
            out[raw_name] = {
                'status': 'new', 'employeenumber': None, 'name': None, 'candidates': [],
            }
    return out

# ══════════════════════════════════════════════════════════════════════════════
#  Supervised Random Forest recommender
# ══════════════════════════════════════════════════════════════════════════════
#
# Learning problem (one classifier for faculty, one for rooms):
#
#   For every past term T and every subject offering o scheduled in T, each
#   candidate c (a faculty member who taught in T / a room in the Room master)
#   becomes one training row:
#
#       x = features(o, c | history strictly BEFORE T)
#       y = 1 if c was the faculty/room actually assigned to o in T, else 0
#
#   The label is the real historical outcome, not a formula of the features, and
#   features never look at T itself or anything after it (time-ordered training,
#   no leakage). At recommendation time the features are computed from ALL history
#   and every active faculty member / room is scored with P(y = 1); candidates are
#   ranked by that probability. Because the features include program/year-level,
#   subject-prefix and subject-name similarity signals, the model can recommend a
#   faculty member or room even for a pairing never seen before (cold start).

from collections import Counter as _Counter, defaultdict as _defaultdict
import random as _random

try:
    from sklearn.ensemble import RandomForestClassifier as _RFC
except ImportError:  # SKLEARN_OK already False in that case
    _RFC = None

# Hyperparameters, selected on the AY2024–25 validation term (see thesis Ch.3).
# Evaluation harnesses may override these dicts; production never does.
# 18-setting grid (trees {100,300} x depth {8,12,None} x leaf {1,3,5}); rule =
# highest validation MRR per model. Faculty: val Top-3 76.75%, MRR 0.6469.
# Room: val Top-3 69.52%, MRR 0.5438.
RF_FAC_PARAMS  = {'n_estimators': 100, 'max_depth': 8, 'min_samples_leaf': 3,
                  'class_weight': None}
RF_ROOM_PARAMS = {'n_estimators': 300, 'max_depth': 8, 'min_samples_leaf': 1,
                  'class_weight': None}
RF_RANDOM_STATE = 42
# Negative sampling: every "related" negative is kept; this many unrelated
# candidates are sampled per offering (keeps training fast and balanced).
RF_EASY_NEGATIVES = 10

# Faculty-profile specialization as model input (Faculty.SpecializationID):
#   None       - not used
#   'rule'     - the scheduler's fixed specialization/subject-prefix rule (0/1)
#   'affinity' - learned from history: share of this subject's / prefix's past
#                assignments taught by faculty with the candidate's specialization
RF_SPEC_MODE = None

SPEC_FEATURES = {
    None:       [],
    'rule':     ['spec_rule_match',        # _spec_matches_subject(profile spec, subject)
                 'has_specialization'],
    'affinity': ['spec_subject_affinity',  # same-specialization share of the subject's history
                 'spec_prefix_affinity',   # same-specialization share of the prefix's history
                 'has_specialization'],
}


def fac_feature_names():
    return FAC_FEATURES + SPEC_FEATURES[RF_SPEC_MODE]


FAC_FEATURES = [
    'subj_count',          # times this faculty taught this subject
    'subj_share',          # share of the subject's past offerings taught by them
    'subj_specialization', # subj_count / faculty's total offerings
    'subj_recency',        # 1/(1+terms since they last taught it); 0 = never
    'prefix_count',        # offerings with the same subject-code prefix (e.g. COMP)
    'prefix_ratio',
    'program_count',       # offerings taught in the same program
    'program_ratio',
    'program_year_count',  # offerings taught in the same program + year level
    'total_offerings',
    'activity_recency',    # 1/(1+terms since they last taught anything)
    'distinct_subjects',
    'name_similarity',     # max word overlap with other subjects they taught
    'subject_history',     # how many past offerings the subject has (context)
    'subject_faculty_pool',# how many faculty ever taught the subject (context)
    'is_lab_subject',
]
ROOM_FEATURES = [
    'subj_count',          # times this room hosted this subject
    'subj_share',
    'subj_recency',
    'program_year_count',  # times it hosted this program + year level
    'program_count',
    'program_share',
    'prefix_count',
    'room_total',
    'prev_term_same_group',   # used by this program+year level last regular term
    'prev_term_cohort',       # used by the same cohort (year level - 1) last regular term
    'room_is_lab',
    'is_lab_subject',
    'lab_type_match',
    'program_floor_share',    # share of the program's classes on this room's floor
]

_STOP = {'AND', 'THE', 'OF', 'NG', 'SA', 'FOR', 'IN', 'TO', 'WITH', 'AT', 'ANG', 'MGA'}
_SEM_ORDER = {'A': 1, 'B': 2, 'C': 3}


def _words(name):
    return frozenset(w for w in _re.findall(r'[A-Z]+', (name or '').upper())
                     if len(w) > 2 and w not in _STOP)


def _prefix(sc):
    m = _re.match(r'[A-Z]+', sc or '')
    return m.group(0) if m else (sc or '')


def _room_key(name):
    return _re.sub(r'[^A-Z0-9]', '', (name or '').upper())


def _room_floor(key):
    m = _re.match(r'[A-Z]+(\d)', key or '')
    return int(m.group(1)) if m else -1


def _norm_prog(p):
    return _re.sub(r'\s+\d+$', '', (p or '').strip()).upper()


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _spec_rule(spec_name, subject_code):
    """The scheduler's specialization rule (imported lazily: scheduler is heavy)."""
    from scheduler import _spec_matches_subject
    return _spec_matches_subject(spec_name or '', (subject_code or '').upper())


def _load_fac_specs(cur):
    cur.execute("""
        SELECT CAST(f.EmployeeNumber AS TEXT) AS empnum,
               TRIM(COALESCE(s.SpecializationName, '')) AS spec
        FROM Faculty f LEFT JOIN Specialization s ON s.SpecializationID = f.SpecializationID
    """)
    return {r['empnum']: r['spec'] for r in cur.fetchall() or []}


def _recency(gap):
    return 1.0 / (1.0 + gap) if gap is not None and gap >= 0 else 0.0


class _History:
    """Cumulative assignment statistics up to (not including) the current term."""

    def __init__(self):
        self.fs = _Counter(); self.fs_last = {}
        self.fp = _Counter(); self.fprog = _Counter(); self.fpy = _Counter()
        self.ftot = _Counter(); self.f_last = {}
        self.f_subj = _defaultdict(set)
        self.s_tot = _Counter(); self.s_fac = _defaultdict(set)
        self.rs = _Counter(); self.rs_last = {}
        self.rpy = _Counter(); self.rprog = _Counter(); self.rp = _Counter()
        self.rtot = _Counter(); self.s_rtot = _Counter()
        self.prog_rtot = _Counter(); self.prog_floor = _Counter()
        self.prev_reg_rpy = _Counter()
        self.sc_words = {}
        self.sc_lab = {}
        self.fac_spec = {}            # empnum -> profile specialization ('' = none)
        self.spec_s = _Counter(); self.spec_p = _Counter()
        self.s_assign = _Counter(); self.p_assign = _Counter()

    def add_term(self, idx, offerings, regular, room_floor):
        term_rpy = _Counter()
        for o in offerings:
            sc, pfx, prog, yl = o['sc'], o['pfx'], o['prog'], o['yl']
            for f in o['emps']:
                sp = self.fac_spec.get(f, '')
                self.s_assign[sc] += 1; self.p_assign[pfx] += 1
                if sp:
                    self.spec_s[(sp, sc)] += 1; self.spec_p[(sp, pfx)] += 1
                self.fs[(f, sc)] += 1; self.fs_last[(f, sc)] = idx
                self.fp[(f, pfx)] += 1; self.fprog[(f, prog)] += 1
                self.fpy[(f, prog, yl)] += 1; self.ftot[f] += 1
                self.f_last[f] = idx; self.f_subj[f].add(sc)
                self.s_fac[sc].add(f)
            if o['emps']:
                self.s_tot[sc] += 1
            for r in o['rooms']:
                self.rs[(r, sc)] += 1; self.rs_last[(r, sc)] = idx
                self.rpy[(r, prog, yl)] += 1; self.rprog[(r, prog)] += 1
                self.rp[(r, pfx)] += 1; self.rtot[r] += 1
                self.prog_rtot[prog] += 1
                self.prog_floor[(prog, room_floor.get(r, -1))] += 1
                term_rpy[(r, prog, yl)] += 1
            if o['rooms']:
                self.s_rtot[sc] += 1
            if o['words']:
                self.sc_words[sc] = o['words']
            self.sc_lab[sc] = self.sc_lab.get(sc, False) or o['lab']
        if regular:
            self.prev_reg_rpy = term_rpy

    # ── feature vectors ─────────────────────────────────────────────────────
    def fac_features(self, idx, sc, pfx, words, prog, yl, lab, f, sim_cache, raw_code=''):
        base = self._fac_base(idx, sc, pfx, words, prog, yl, lab, f, sim_cache)
        if RF_SPEC_MODE is None:
            return base
        sp = self.fac_spec.get(f, '')
        if RF_SPEC_MODE == 'rule':
            return base + [1.0 if _spec_rule(sp, raw_code or sc) else 0.0, 1.0 if sp else 0.0]
        return base + [
            self.spec_s.get((sp, sc), 0) / max(self.s_assign.get(sc, 0), 1) if sp else 0.0,
            self.spec_p.get((sp, pfx), 0) / max(self.p_assign.get(pfx, 0), 1) if sp else 0.0,
            1.0 if sp else 0.0,
        ]

    def _fac_base(self, idx, sc, pfx, words, prog, yl, lab, f, sim_cache):
        n_subj = self.fs.get((f, sc), 0)
        tot = self.ftot.get(f, 0)
        totd = max(tot, 1)
        last = self.fs_last.get((f, sc))
        flast = self.f_last.get(f)
        key = (f, sc)
        sim = sim_cache.get(key)
        if sim is None:
            sim = 0.0
            if words:
                for g in self.f_subj.get(f, ()):
                    if g == sc:
                        continue
                    gw = self.sc_words.get(g)
                    if gw:
                        j = len(words & gw) / len(words | gw)
                        if j > sim:
                            sim = j
            sim_cache[key] = sim
        n_pfx = self.fp.get((f, pfx), 0)
        n_prog = self.fprog.get((f, prog), 0)
        return [
            float(n_subj),
            n_subj / max(self.s_tot.get(sc, 0), 1),
            n_subj / totd,
            _recency(idx - last) if last is not None else 0.0,
            float(n_pfx),
            n_pfx / totd,
            float(n_prog),
            n_prog / totd,
            float(self.fpy.get((f, prog, yl), 0)),
            float(tot),
            _recency(idx - flast) if flast is not None else 0.0,
            float(len(self.f_subj.get(f, ()))),
            sim,
            float(self.s_tot.get(sc, 0)),
            float(len(self.s_fac.get(sc, ()))),
            1.0 if lab else 0.0,
        ]

    def room_features(self, idx, sc, pfx, prog, yl, lab, r, r_is_lab, r_floor):
        n_subj = self.rs.get((r, sc), 0)
        last = self.rs_last.get((r, sc))
        n_prog = self.rprog.get((r, prog), 0)
        ptot = self.prog_rtot.get(prog, 0)
        return [
            float(n_subj),
            n_subj / max(self.s_rtot.get(sc, 0), 1),
            _recency(idx - last) if last is not None else 0.0,
            float(self.rpy.get((r, prog, yl), 0)),
            float(n_prog),
            n_prog / max(ptot, 1),
            float(self.rp.get((r, pfx), 0)),
            float(self.rtot.get(r, 0)),
            float(self.prev_reg_rpy.get((r, prog, yl), 0)),
            float(self.prev_reg_rpy.get((r, prog, (yl - 1) if yl is not None else None), 0)),
            1.0 if r_is_lab else 0.0,
            1.0 if lab else 0.0,
            1.0 if bool(r_is_lab) == bool(lab) else 0.0,
            self.prog_floor.get((prog, r_floor), 0) / max(ptot, 1),
        ]


# ── Module-level cache ────────────────────────────────────────────────────────

_rf_fac_model       = None
_rf_room_model      = None
_rf_hist            = None     # _History over ALL terms (inference state)
_rf_next_idx        = 0        # term index of "the term being scheduled"
_rf_rooms           = {}       # room_key -> {'is_lab': bool, 'floor': int}
_rf_train_info      = {}       # sizes / class balance, for reports and tests
_rf_ambiguous_names = set()

# Wall-clock time of the last successful training run (see train_rf_dss).
_rf_trained_at      = 0.0

# How long a trained model stays in cache before the next /api/dss/suggest
# call triggers a retrain, so new historical imports / published schedules are
# picked up without every write path having to call reset_rf_cache().
RF_CACHE_TTL_SECONDS = 15 * 60


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_rooms(cur):
    cur.execute("SELECT RoomName, COALESCE(RoomType, 'Lecture') AS roomtype FROM Room")
    rooms = {}
    for r in cur.fetchall() or []:
        k = _room_key(r['roomname'])
        if k:
            rooms[k] = {'is_lab': (r['roomtype'] or '') == 'Laboratory', 'floor': _room_floor(k)}
    return rooms


def _map_rooms(raw, rooms):
    """Historical room text -> list of Room-master keys ('TBA'/unknown -> [])."""
    raw = (raw or '').upper().strip()
    if not raw:
        return []
    k = _room_key(raw)
    if k in rooms:
        return [k]
    if '/' in raw:
        return [p for p in (_room_key(x) for x in raw.split('/')) if p in rooms]
    return []


def _load_events(cur, rooms):
    """All past assignments as uniform event dicts, from historical_data plus
    Published schedule-table terms that are not already in historical_data."""
    name_to_empnum, ambiguous = _build_name_to_empnum(cur)
    events = []
    cur.execute("""
        SELECT h.academicyearid AS ay, COALESCE(s.semestertype, '') AS sem,
               REGEXP_REPLACE(UPPER(TRIM(h."Subject Code")), '[^A-Z0-9]', '', 'g') AS sc,
               UPPER(TRIM(h."Subject Code")) AS raw, h."Subject Name" AS sname, COALESCE(h."Laboratory Hours", 0) AS labh,
               h."Program" AS prog, h."Year Level" AS yl, h.sectionid, h."Course" AS course,
               UPPER(TRIM(COALESCE(h."Instructor", ''))) AS inst,
               NULLIF(TRIM(COALESCE(h.employeenumber, '')), '') AS emp,
               h."Room" AS room
        FROM historical_data h
        LEFT JOIN semester s ON s.semesterid = h.semesterid
        WHERE h."Subject Code" IS NOT NULL AND TRIM(h."Subject Code") != ''
          AND h.academicyearid IS NOT NULL AND TRIM(h.academicyearid) != ''
    """)
    for r in cur.fetchall() or []:
        emp = r['emp'] or (name_to_empnum.get(r['inst']) if r['inst'] not in ambiguous else None)
        events.append({
            'ay': r['ay'], 'sem': (r['sem'] or '').upper(), 'sc': r['sc'], 'raw': r['raw'],
            'sname': r['sname'], 'lab': (r['labh'] or 0) > 0,
            'prog': _norm_prog(r['prog']), 'yl': _to_int(r['yl']),
            'sect': r['sectionid'] if r['sectionid'] is not None else ('C', (r['course'] or '').upper()),
            'emp': str(emp) if emp else None,
            'rooms': _map_rooms(r['room'], rooms),
        })
    hist_terms = {(e['ay'], e['sem']) for e in events}

    try:
        cur.execute("""
            SELECT sm.academicyearid AS ay, COALESCE(sm.semestertype, '') AS sem,
                   REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g') AS sc,
                   UPPER(TRIM(cs.subjectcode)) AS raw, cs.subjectname AS sname, COALESCE(cs.laboratoryhours, 0) AS labh,
                   pyl.programcode AS prog, pyl.yearlevel AS yl, sch.sectionid,
                   CAST(COALESCE(ss.employeenumber, sch.employeenumber) AS TEXT) AS emp, r.roomname AS room
            FROM schedule sch
            JOIN curriculumsubject cs ON sch.curriculumsubjectid = cs.curriculumsubjectid
            JOIN semester sm          ON sch.semesterid = sm.semesterid
            JOIN schedule_version sv  ON sv.scheduleid = sch.scheduleid
            LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid
            LEFT JOIN room r               ON r.roomid = ss.roomid
            LEFT JOIN sections sec          ON sch.sectionid = sec.sectionid
            LEFT JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
            WHERE sv.status IN ('Published', 'Archive')
              AND COALESCE(sv.original_status, sv.status) <> 'Draft'
              AND cs.subjectcode IS NOT NULL AND TRIM(cs.subjectcode) != ''
        """)
        for r in cur.fetchall() or []:
            if (r['ay'], (r['sem'] or '').upper()) in hist_terms:
                continue
            events.append({
                'ay': r['ay'], 'sem': (r['sem'] or '').upper(), 'sc': r['sc'], 'raw': r['raw'],
                'sname': r['sname'], 'lab': (r['labh'] or 0) > 0,
                'prog': _norm_prog(r['prog']), 'yl': _to_int(r['yl']),
                'sect': r['sectionid'], 'emp': r['emp'] or None,
                'rooms': [k for k in [_room_key(r['room'])] if k in rooms],
            })
    except Exception:
        cur.connection.rollback()
    return events, ambiguous


def _group_terms(events):
    """events -> chronologically sorted [(term, [offering, ...])].
    An offering = one (section, subject) in a term; lecture/lab rows are merged."""
    by_term = _defaultdict(dict)
    for e in events:
        term = (e['ay'], e['sem'])
        okey = (e['sect'], e['sc'])
        o = by_term[term].get(okey)
        if o is None:
            o = by_term[term][okey] = {
                'sc': e['sc'], 'raw': e['raw'], 'pfx': _prefix(e['sc']), 'prog': e['prog'], 'yl': e['yl'],
                'words': _words(e['sname']), 'lab': False, 'emps': set(), 'rooms': set()}
        o['lab'] = o['lab'] or e['lab']
        if e['emp']:
            o['emps'].add(e['emp'])
        o['rooms'].update(e['rooms'])
        if not o['words'] and e['sname']:
            o['words'] = _words(e['sname'])
    order = sorted(by_term, key=lambda t: (_ay_to_int(t[0]), _SEM_ORDER.get(t[1], 0)))
    out = []
    for t in order:
        offs = sorted(by_term[t].values(), key=lambda o: (o['sc'], o['prog'], o['yl'] or 0,
                                                          sorted(o['emps']), sorted(o['rooms'])))
        out.append((t, offs))
    return out


def _build_training_set(terms, rooms, fac_spec=None):
    hist = _History()
    hist.fac_spec = fac_spec or {}
    room_floor = {k: v['floor'] for k, v in rooms.items()}
    room_keys = sorted(rooms)
    Xf, yf, Xr, yr = [], [], [], []
    for idx, (term, offs) in enumerate(terms):
        if idx > 0:
            rng = _random.Random(RF_RANDOM_STATE * 1000 + idx)
            pool = sorted({f for o in offs for f in o['emps']})
            sim_cache = {}
            for o in offs:
                sc, pfx, prog, yl, lab = o['sc'], o['pfx'], o['prog'], o['yl'], o['lab']
                words = o['words'] or hist.sc_words.get(sc, frozenset())
                if o['emps']:
                    hard, easy = [], []
                    for f in pool:
                        if f in o['emps']:
                            continue
                        if (hist.fs.get((f, sc)) or hist.fp.get((f, pfx))
                                or hist.fpy.get((f, prog, yl))):
                            hard.append(f)
                        else:
                            easy.append(f)
                    negs = hard + (rng.sample(easy, RF_EASY_NEGATIVES)
                                   if len(easy) > RF_EASY_NEGATIVES else easy)
                    for f in sorted(o['emps']):
                        Xf.append(hist.fac_features(idx, sc, pfx, words, prog, yl, lab, f, sim_cache, o['raw'])); yf.append(1)
                    for f in negs:
                        Xf.append(hist.fac_features(idx, sc, pfx, words, prog, yl, lab, f, sim_cache, o['raw'])); yf.append(0)
                if o['rooms']:
                    hard, easy = [], []
                    for r in room_keys:
                        if r in o['rooms']:
                            continue
                        if (hist.rs.get((r, sc)) or hist.rpy.get((r, prog, yl))
                                or hist.rp.get((r, pfx)) or hist.prev_reg_rpy.get((r, prog, yl))):
                            hard.append(r)
                        else:
                            easy.append(r)
                    negs = hard + (rng.sample(easy, RF_EASY_NEGATIVES)
                                   if len(easy) > RF_EASY_NEGATIVES else easy)
                    for r in sorted(o['rooms']):
                        Xr.append(hist.room_features(idx, sc, pfx, prog, yl, lab, r,
                                                     rooms[r]['is_lab'], rooms[r]['floor'])); yr.append(1)
                    for r in negs:
                        Xr.append(hist.room_features(idx, sc, pfx, prog, yl, lab, r,
                                                     rooms[r]['is_lab'], rooms[r]['floor'])); yr.append(0)
        hist.add_term(idx, offs, term[1] in ('A', 'B'), room_floor)
    return hist, Xf, yf, Xr, yr


# ── Training ──────────────────────────────────────────────────────────────────

def train_rf_dss():
    """Train the faculty and room Random Forest classifiers on past assignment
    outcomes. Lazy-trained; cached for RF_CACHE_TTL_SECONDS after each run."""
    global _rf_fac_model, _rf_room_model, _rf_hist, _rf_next_idx, _rf_rooms
    global _rf_train_info, _rf_ambiguous_names, _rf_trained_at

    if not SKLEARN_OK or _RFC is None:
        return
    if _rf_hist is not None and (_time.time() - _rf_trained_at) < RF_CACHE_TTL_SECONDS:
        return

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        rooms = _load_rooms(cur)
        events, ambiguous = _load_events(cur, rooms)
        terms = _group_terms(events)
        fac_spec = _load_fac_specs(cur) if RF_SPEC_MODE else {}
        hist, Xf, yf, Xr, yr = _build_training_set(terms, rooms, fac_spec)

        fac_model = room_model = None
        if len(set(yf)) == 2:
            fac_model = _RFC(random_state=RF_RANDOM_STATE, n_jobs=-1, **RF_FAC_PARAMS)
            fac_model.fit(_np.asarray(Xf, dtype=float), _np.asarray(yf))
        if len(set(yr)) == 2:
            room_model = _RFC(random_state=RF_RANDOM_STATE, n_jobs=-1, **RF_ROOM_PARAMS)
            room_model.fit(_np.asarray(Xr, dtype=float), _np.asarray(yr))

        # Parallel fit, single-threaded predict: each request scores only ~150
        # candidates, where thread start-up would cost more than the work itself.
        for m in (fac_model, room_model):
            if m is not None:
                m.set_params(n_jobs=1)
        _rf_fac_model, _rf_room_model = fac_model, room_model
        _rf_hist, _rf_next_idx, _rf_rooms = hist, len(terms), rooms
        _rf_ambiguous_names = ambiguous
        _rf_train_info = {
            'terms': [f'{t[0]}-{t[1]}' for t, _ in terms],
            'faculty_rows': len(yf), 'faculty_positives': int(sum(yf)),
            'room_rows': len(yr), 'room_positives': int(sum(yr)),
            'spec_mode': RF_SPEC_MODE, 'faculty_features': fac_feature_names(),
            'faculty_params': dict(RF_FAC_PARAMS), 'room_params': dict(RF_ROOM_PARAMS),
        }
        # Stamp success only after every step above completed, so a partial
        # failure retries on the next call instead of caching a broken model.
        _rf_trained_at = _time.time()
    except Exception:
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def reset_rf_cache():
    """Discard trained models so the next request triggers a full retrain."""
    global _rf_fac_model, _rf_room_model, _rf_hist, _rf_next_idx, _rf_rooms
    global _rf_train_info, _rf_ambiguous_names, _rf_trained_at
    _rf_fac_model = _rf_room_model = None
    _rf_hist = None
    _rf_next_idx = 0
    _rf_rooms = {}
    _rf_train_info = {}
    _rf_ambiguous_names = set()
    _rf_trained_at = 0.0


# ── Inference & Explainability ────────────────────────────────────────────────

def _subject_context(subject_code, subject_name, is_lab):
    sc = _norm_sc(subject_code or '')
    words = _words(subject_name) if subject_name else frozenset()
    if not words and _rf_hist is not None:
        words = _rf_hist.sc_words.get(sc, frozenset())
    if is_lab is None:
        is_lab = bool(_rf_hist.sc_lab.get(sc)) if _rf_hist is not None else False
    return sc, _prefix(sc), words, bool(is_lab)


def _fac_explanation(subject_code, feat, prob):
    n, recency = int(feat[0]), feat[3]
    parts = [f"Predicted assignment probability {prob * 100:.1f}%."]
    if n:
        parts.append(f"Taught {subject_code} {n} time(s) before.")
        if recency:
            gap = round(1 / recency - 1)
            parts.append("Taught it last term." if gap <= 1 else f"Last taught it {gap} term(s) ago.")
    else:
        parts.append(f"Has not taught {subject_code} before.")
        if feat[4]:
            parts.append(f"Taught {int(feat[4])} related subject(s) with the same code prefix.")
        if feat[12] >= 0.3:
            parts.append("Taught a subject with a similar title.")
    if feat[8]:
        parts.append(f"Taught this program and year level {int(feat[8])} time(s).")
    return {
        'probability':               round(prob, 4),
        'exact_subject_match_count': n,
        'specialization_ratio':      round(feat[2], 4),
        'related_prefix_count':      int(feat[4]),
        'program_year_count':        int(feat[8]),
        'name_similarity':           round(feat[12], 3),
        'explanation':               ' '.join(parts),
    }


def rf_rank_faculty(subject_code, employee_numbers, program=None, year_level=None,
                    is_lab=None, subject_name=None):
    """Score every candidate faculty member for one subject offering.

    Returns {employee_number(str): {'score': P(assigned), 'rf_explanation': {...}}};
    an empty dict when no model is trained."""
    if _rf_fac_model is None or _rf_hist is None:
        return {}
    emps = [str(e) for e in employee_numbers if e is not None]
    if not emps:
        return {}
    sc, pfx, words, lab = _subject_context(subject_code, subject_name, is_lab)
    prog, yl = _norm_prog(program), _to_int(year_level)
    cache = {}
    X = [_rf_hist.fac_features(_rf_next_idx, sc, pfx, words, prog, yl, lab, e, cache,
                               (subject_code or '').upper().strip()) for e in emps]
    probs = _rf_fac_model.predict_proba(_np.asarray(X, dtype=float))[:, 1]
    return {e: {'score': float(p), 'rf_explanation': _fac_explanation(subject_code, x, float(p))}
            for e, x, p in zip(emps, X, probs)}


def rf_rank_rooms(subject_code, room_names, program=None, year_level=None, is_lab=None):
    """Score every candidate room for one subject offering.

    Returns {room_name: P(assigned)}; rooms not in the Room master score 0.0;
    an empty dict when no model is trained."""
    if _rf_room_model is None or _rf_hist is None:
        return {}
    sc, pfx, _w, lab = _subject_context(subject_code, None, is_lab)
    prog, yl = _norm_prog(program), _to_int(year_level)
    names, X = [], []
    out = {}
    for name in room_names:
        k = _room_key(name)
        info = _rf_rooms.get(k)
        if info is None:
            out[name] = 0.0
            continue
        names.append(name)
        X.append(_rf_hist.room_features(_rf_next_idx, sc, pfx, prog, yl, lab, k,
                                        info['is_lab'], info['floor']))
    if X:
        probs = _rf_room_model.predict_proba(_np.asarray(X, dtype=float))[:, 1]
        out.update({n: float(p) for n, p in zip(names, probs)})
    return out


def rf_get_faculty_info(subject_code: str, instructor_name: str, hist_name: str = None,
                        employee_number=None, program=None, year_level=None) -> dict:
    """Single-candidate wrapper around rf_rank_faculty (kept for older callers)."""
    if employee_number is None:
        return {'score': 0.0}
    res = rf_rank_faculty(subject_code, [employee_number], program, year_level)
    return res.get(str(employee_number), {'score': 0.0})


def rf_score_faculty(subject_code: str, instructor_name: str,
                     hist_name: str = None, employee_number=None) -> float:
    """Score only — convenience wrapper around rf_get_faculty_info."""
    return rf_get_faculty_info(subject_code, instructor_name, hist_name, employee_number)['score']


def rf_score_room(subject_code: str, room_name: str, program=None, year_level=None) -> float:
    """P(assigned) for one (subject, room) pair; 0.0 if untrained or unknown room."""
    return rf_rank_rooms(subject_code, [room_name], program, year_level).get(room_name, 0.0)
