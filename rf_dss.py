import re as _re

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
    cur.execute("""
        SELECT CAST(EmployeeNumber AS TEXT)              AS empnum,
               LOWER(TRIM(LastName))                    AS last_name,
               LOWER(TRIM(COALESCE(FirstName, '')))     AS first_name
        FROM Faculty
        WHERE EmployeeStatus != 'Archived'
    """)
    faculty = cur.fetchall() or []

    by_last = {}
    for f in faculty:
        by_last.setdefault(f['last_name'], []).append(f)

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
        key        = row['inst_key']
        # If the stored employee number is already in historical_data, use it directly.
        if row.get('employeenumber'):
            name_to_empnum[key] = str(row['employeenumber'])
            continue
        last, fi   = _parse_inst_name(row['inst_raw'])

        if not last:
            ambiguous.add(key)
            continue

        candidates = by_last.get(last, [])
        if not candidates:
            # No Faculty record with that surname — cannot resolve
            continue

        if len(candidates) == 1:
            fac = candidates[0]
            # Verify first name if we have one and the Faculty record has a first name.
            # Bidirectional prefix for multi-char names mirrors the app.py matching rule.
            if fi and fac['first_name']:
                fac_fn = fac['first_name']
                if len(fi) > 1 and len(fac_fn) > 1:
                    first_ok = (fi == fac_fn or fac_fn.startswith(fi) or fi.startswith(fac_fn))
                else:
                    first_ok = (fi[0] == fac_fn[0])
                if not first_ok:
                    ambiguous.add(key)
                    continue
            name_to_empnum[key] = fac['empnum']
        else:
            # Multiple faculty share this surname — use first name to pick one.
            if not fi:
                ambiguous.add(key)
                continue

            def _first_match(fac_fn, hist_fi):
                if not fac_fn:
                    return False
                if len(hist_fi) > 1 and len(fac_fn) > 1:
                    # Bidirectional prefix: covers "JOSE"→"JOSEPH", "MA."→"MARIA", etc.
                    return (hist_fi == fac_fn or
                            fac_fn.startswith(hist_fi) or
                            hist_fi.startswith(fac_fn))
                return hist_fi[0] == fac_fn[0]

            matches = [f for f in candidates if _first_match(f['first_name'], fi)]
            if len(matches) == 1:
                name_to_empnum[key] = matches[0]['empnum']
            else:
                ambiguous.add(key)   # still ambiguous after first-name check

    return name_to_empnum, ambiguous


# ── Module-level cache ────────────────────────────────────────────────────────

_rf_fac_model       = None
_rf_room_model      = None
# Primary index: (norm_sc, empnum_str) → stats dict
_rf_fac_stats       = {}
# Fallback index: (norm_sc, UPPER_INST_NAME) → stats dict
# Only populated for unresolved names that are NOT ambiguous.
_rf_fac_name_stats  = {}
# Names that resolved to multiple possible faculty — never used for inference.
_rf_ambiguous_names = set()
_rf_room_stats      = {}


# ── Training ──────────────────────────────────────────────────────────────────

def train_rf_dss():
    """Build RF regressors from historical_data. Lazy-trained; cached after first call."""
    global _rf_fac_model, _rf_room_model
    global _rf_fac_stats, _rf_fac_name_stats, _rf_ambiguous_names, _rf_room_stats

    if not SKLEARN_OK or _rf_fac_model is not None:
        return

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:

        # ── Step 1: Resolve historical instructor names to employee numbers ────
        name_to_empnum, ambiguous_names = _build_name_to_empnum(cur)
        _rf_ambiguous_names = ambiguous_names

        # ── Step 2: Raw per-(subject, instructor-name) data from historical_data
        cur.execute("""
            SELECT
                REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g') AS sc,
                UPPER(TRIM("Instructor"))                                           AS inst,
                COUNT(*)                                                            AS exact_count,
                MAX(COALESCE(academicyearid, ''))                                   AS latest_ay
            FROM historical_data
            WHERE "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
              AND "Instructor"   IS NOT NULL AND TRIM("Instructor")   != ''
            GROUP BY
                REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g'),
                UPPER(TRIM("Instructor"))
        """)
        raw_pairs = cur.fetchall() or []

        # ── Step 3: Per-instructor-name aggregate stats (before merging by empnum)
        cur.execute("""
            SELECT
                UPPER(TRIM("Instructor"))                                                         AS inst,
                COUNT(*)                                                                          AS total_assignments,
                COUNT(DISTINCT REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g')) AS distinct_subjects
            FROM historical_data
            WHERE "Instructor"   IS NOT NULL AND TRIM("Instructor")   != ''
              AND "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
            GROUP BY UPPER(TRIM("Instructor"))
        """)
        name_i_stats = {
            r['inst']: {'total': int(r['total_assignments']), 'distinct': int(r['distinct_subjects'])}
            for r in (cur.fetchall() or [])
        }

        # ── Step 4: Subject totals
        cur.execute("""
            SELECT
                REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g') AS sc,
                COUNT(*) AS subject_total
            FROM historical_data
            WHERE "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
              AND "Instructor"   IS NOT NULL AND TRIM("Instructor")   != ''
            GROUP BY REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g')
        """)
        s_totals = {r['sc']: int(r['subject_total']) for r in (cur.fetchall() or [])}

        # ── Step 5: Max AY for recency
        cur.execute("SELECT COALESCE(MAX(academicyearid), '') AS max_ay FROM historical_data")
        max_ay     = (cur.fetchone() or {}).get('max_ay', '')
        max_ay_int = _ay_to_int(max_ay)
        max_yr     = _ay_start_year(max_ay)

        # ── Step 5b: Pull ALL teaching data from the schedule table ───────────
        # historical_data is import-based and may lag behind the current system.
        # The schedule table holds Published/Archive records with direct empnums.
        # We fetch them here (before Step 6 so the updated max_ay is used for
        # the recent-count threshold) and merge into buckets after Step 7.
        schedule_pairs = []          # [{sc, empnum, sched_count, latest_ay}, …]
        sched_i_raw    = {}          # empnum → {total, distinct}
        sched_recent   = {}          # (sc, empnum) → recent_count
        try:
            # 5b-1: True global max AY (schedule table may be more recent than historical_data)
            cur.execute("""
                SELECT COALESCE(MAX(academicyearid), '') AS max_ay
                FROM semester
                WHERE academicyearid IS NOT NULL AND TRIM(academicyearid) != ''
            """)
            sched_max = (cur.fetchone() or {}).get('max_ay', '')
            if sched_max and sched_max > max_ay:
                max_ay     = sched_max
                max_ay_int = _ay_to_int(max_ay)
                max_yr     = _ay_start_year(max_ay)

            # 5b-2: Per-(subject, empnum) assignment counts from schedule table
            cur.execute("""
                SELECT
                    REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g') AS sc,
                    CAST(sch.employeenumber AS TEXT)                                    AS empnum,
                    COUNT(*)                                                            AS sched_count,
                    MAX(COALESCE(sm.academicyearid, ''))                               AS latest_ay
                FROM schedule sch
                JOIN curriculumsubject cs ON sch.curriculumsubjectid = cs.curriculumsubjectid
                JOIN schedule_version sv  ON sv.scheduleid = sch.scheduleid
                JOIN semester sm          ON sch.semesterid = sm.semesterid
                WHERE sch.employeenumber IS NOT NULL
                  AND cs.subjectcode IS NOT NULL AND TRIM(cs.subjectcode) != ''
                  AND sv.status IN ('Published', 'Archive')
                GROUP BY
                    REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g'),
                    CAST(sch.employeenumber AS TEXT)
            """)
            schedule_pairs = cur.fetchall() or []

            # 5b-3: Extend s_totals so subject_share denominators cover all years
            cur.execute("""
                SELECT
                    REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g') AS sc,
                    COUNT(*) AS subject_total
                FROM schedule sch
                JOIN curriculumsubject cs ON sch.curriculumsubjectid = cs.curriculumsubjectid
                JOIN schedule_version sv  ON sv.scheduleid = sch.scheduleid
                WHERE sch.employeenumber IS NOT NULL
                  AND cs.subjectcode IS NOT NULL AND TRIM(cs.subjectcode) != ''
                  AND sv.status IN ('Published', 'Archive')
                GROUP BY REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g')
            """)
            for r in (cur.fetchall() or []):
                s_totals[r['sc']] = s_totals.get(r['sc'], 0) + int(r['subject_total'])

            # 5b-4: Per-empnum total assignment and specialization counts
            cur.execute("""
                SELECT
                    CAST(sch.employeenumber AS TEXT) AS empnum,
                    COUNT(*)                         AS total_assignments,
                    COUNT(DISTINCT REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g'))
                                                     AS distinct_subjects
                FROM schedule sch
                JOIN curriculumsubject cs ON sch.curriculumsubjectid = cs.curriculumsubjectid
                JOIN schedule_version sv  ON sv.scheduleid = sch.scheduleid
                WHERE sch.employeenumber IS NOT NULL
                  AND cs.subjectcode IS NOT NULL AND TRIM(cs.subjectcode) != ''
                  AND sv.status IN ('Published', 'Archive')
                GROUP BY CAST(sch.employeenumber AS TEXT)
            """)
            for r in (cur.fetchall() or []):
                sched_i_raw[r['empnum']] = {
                    'total':    int(r['total_assignments']),
                    'distinct': int(r['distinct_subjects']),
                }

            # 5b-5: Counts from schedule across ALL available AYs (no recency cutoff)
            cur.execute("""
                SELECT
                    REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g') AS sc,
                    CAST(sch.employeenumber AS TEXT)                                    AS empnum,
                    COUNT(*)                                                            AS recent_count
                FROM schedule sch
                JOIN curriculumsubject cs ON sch.curriculumsubjectid = cs.curriculumsubjectid
                JOIN schedule_version sv  ON sv.scheduleid = sch.scheduleid
                WHERE sch.employeenumber IS NOT NULL
                  AND cs.subjectcode IS NOT NULL AND TRIM(cs.subjectcode) != ''
                  AND sv.status IN ('Published', 'Archive')
                GROUP BY
                    REGEXP_REPLACE(UPPER(TRIM(cs.subjectcode)), '[^A-Z0-9]', '', 'g'),
                    CAST(sch.employeenumber AS TEXT)
            """)
            for r in (cur.fetchall() or []):
                sched_recent[(r['sc'], r['empnum'])] = int(r['recent_count'])
        except Exception:
            pass

        # ── Step 6: Counts per (sc, inst-name) across ALL years in historical data
        raw_recent = {}
        try:
            cur.execute("""
                SELECT
                    REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g') AS sc,
                    UPPER(TRIM("Instructor"))                                           AS inst,
                    COUNT(*)                                                            AS recent_count
                FROM historical_data
                WHERE "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
                  AND "Instructor"   IS NOT NULL AND TRIM("Instructor")   != ''
                GROUP BY
                    REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g'),
                    UPPER(TRIM("Instructor"))
            """)
            raw_recent = {
                (r['sc'], r['inst']): int(r['recent_count'])
                for r in (cur.fetchall() or [])
            }
        except Exception:
            pass

        # ── Step 7: Merge raw name-based data → empnum-based buckets ──────────
        #
        # Each raw_pair row has an instructor name (inst).
        # If inst resolves to an empnum, bucket by (sc, empnum).
        # If inst is unambiguous but unresolved (no Faculty match), bucket by (sc, inst)
        #   and mark as name-only (safe for name-based fallback lookup).
        # If inst is ambiguous, skip — cannot safely attribute to one faculty.
        #
        # Merging is needed because the same faculty might appear under multiple name
        # spellings in historical_data (e.g., 'SANTOS, J.' and 'SANTOS, JOSE').

        # bucket key → aggregated fields
        buckets     = {}   # key → {exact_count, latest_ay, inst_names: set}
        bucket_type = {}   # key → 'id' | 'name'

        for r in raw_pairs:
            sc   = r['sc']
            inst = r['inst']
            cnt  = int(r['exact_count'])
            ay   = r['latest_ay'] or ''

            if inst in name_to_empnum:
                empnum = name_to_empnum[inst]
                key    = (sc, empnum)
                btype  = 'id'
            elif inst in ambiguous_names:
                continue   # ambiguous — skip entirely
            else:
                # Unresolved but unambiguous name — keep as name-based bucket
                key   = (sc, inst)
                btype = 'name'

            if key not in buckets:
                buckets[key]     = {'exact_count': 0, 'latest_ay': '', 'inst_names': set()}
                bucket_type[key] = btype
            b = buckets[key]
            b['exact_count'] += cnt
            if ay > b['latest_ay']:
                b['latest_ay'] = ay
            b['inst_names'].add(inst)

        # ── Step 7b: Merge schedule-table pairs into buckets ──────────────────
        # These are keyed directly by empnum so no name resolution is needed.
        for r in schedule_pairs:
            sc     = r['sc']
            empnum = r['empnum']
            cnt    = int(r['sched_count'])
            ay     = r['latest_ay'] or ''
            key    = (sc, empnum)
            if key in buckets:
                b = buckets[key]
                b['exact_count'] += cnt
                if ay > b['latest_ay']:
                    b['latest_ay'] = ay
            else:
                buckets[key]     = {'exact_count': cnt, 'latest_ay': ay, 'inst_names': set()}
                bucket_type[key] = 'id'

        # ── Step 8: Per-empnum aggregated instructor stats ────────────────────
        #
        # We need total_assignments and distinct_subjects per empnum (or name key).
        # Strategy: sum total_assignments for all name variants that map to the same empnum.
        # For distinct_subjects: use MAX across variants (avoids double-counting the same subjects).
        empnum_i_stats = {}   # empnum_str → {'total', 'distinct'}
        for inst, istats in name_i_stats.items():
            if inst in ambiguous_names:
                continue
            key = name_to_empnum.get(inst, inst)   # empnum string or raw name
            if key not in empnum_i_stats:
                empnum_i_stats[key] = {'total': 0, 'distinct': 0}
            empnum_i_stats[key]['total']    += istats['total']
            empnum_i_stats[key]['distinct']  = max(
                empnum_i_stats[key]['distinct'], istats['distinct']
            )

        # ── Step 8b: Merge schedule-based instructor totals ───────────────────
        for empnum, si in sched_i_raw.items():
            if empnum not in empnum_i_stats:
                empnum_i_stats[empnum] = {'total': 0, 'distinct': 0}
            empnum_i_stats[empnum]['total']    += si['total']
            empnum_i_stats[empnum]['distinct']  = max(
                empnum_i_stats[empnum]['distinct'], si['distinct']
            )

        # ── Step 9: Per-(sc, empnum) recent counts ────────────────────────────
        empnum_recent = {}   # (sc, empnum_or_name) → recent_count
        for (sc, inst), cnt in raw_recent.items():
            if inst in ambiguous_names:
                continue
            key = (sc, name_to_empnum.get(inst, inst))
            empnum_recent[key] = empnum_recent.get(key, 0) + cnt

        # ── Step 9b: Merge schedule-based recent counts ───────────────────────
        for (sc, empnum), cnt in sched_recent.items():
            key = (sc, empnum)
            empnum_recent[key] = empnum_recent.get(key, 0) + cnt

        # ── Step 10: Subject-code prefix affinity per empnum ──────────────────
        empnum_prefix = {}   # empnum_or_name → {prefix → count}
        for (sc, key), b in buckets.items():
            pfx = sc[:2] if len(sc) >= 2 else sc
            d   = empnum_prefix.setdefault(key if bucket_type[(sc, key)] == 'id' else key, {})
            d[pfx] = d.get(pfx, 0) + b['exact_count']

        # ── Step 11: Build feature vectors and train RF ───────────────────────
        X_f, y_f    = [], []
        id_fstats   = {}    # (norm_sc, empnum_str) → stats
        name_fstats = {}    # (norm_sc, UPPER_NAME) → stats

        for (sc, bucket_key), b in buckets.items():
            btype       = bucket_type[(sc, bucket_key)]
            exact_count = b['exact_count']
            latest_ay   = b['latest_ay']

            ist           = empnum_i_stats.get(bucket_key, {'total': 1, 'distinct': 1})
            total_assign  = max(ist['total'], 1)
            distinct_subj = max(ist['distinct'], 1)

            subject_total = max(s_totals.get(sc, 1), 1)
            recent_count  = empnum_recent.get((sc, bucket_key), 0)

            exact_pct     = exact_count / total_assign
            spec_ratio    = exact_pct
            subject_share = exact_count / subject_total

            inst_yr = _ay_start_year(latest_ay)
            recency_score = (
                max(0.0, 1.0 - (max_yr - inst_yr) / 10.0)
                if max_yr > 0 and inst_yr > 0 else 0.0
            )

            recent_ratio  = recent_count / total_assign

            pfx           = sc[:2] if len(sc) >= 2 else sc
            prefix_total  = empnum_prefix.get(bucket_key, {}).get(pfx, 0)
            related_ratio = prefix_total / total_assign

            generalist_score = 1.0 / max(distinct_subj, 1)

            feat = [
                float(exact_count),   # F0 — exact subject match count (strongest signal)
                exact_pct,            # F1 — fraction of instructor's history on this subject
                spec_ratio,           # F2 — specialization ratio (alias of F1)
                subject_share,        # F3 — instructor's share of all records for this subject
                recency_score,        # F4 — 0–1; 1 = taught in most recent AY
                float(recent_count),  # F5 — count in last 2 academic years
                recent_ratio,         # F6 — F5 / total_assign
                related_ratio,        # F7 — fraction of history sharing same code prefix
                generalist_score,     # F8 — 1/distinct_subjects (high = specialist)
            ]

            # Specialist with high exact count scores higher than a generalist with same count.
            # Recency is already captured as features F4–F6; keeping it out of the target
            # prevents older faculty from being systematically suppressed by the label value.
            target = _np.log1p(exact_count) * (1.0 + spec_ratio)

            X_f.append(feat)
            y_f.append(target)

            entry = {
                'feat':                 feat,
                'exact_count':          exact_count,
                'specialization_ratio': round(spec_ratio, 4),
                'recent_count':         recent_count,
                'latest_ay':            latest_ay,
                'total_assign':         total_assign,
            }

            if btype == 'id':
                id_fstats[(sc, bucket_key)] = entry
            else:
                name_fstats[(sc, bucket_key)] = entry

        if len(X_f) >= 5:
            _rf_fac_model = _RFR(n_estimators=100, random_state=42)
            _rf_fac_model.fit(X_f, y_f)
        _rf_fac_stats      = id_fstats
        _rf_fac_name_stats = name_fstats

        # ── Room RF (frequency-based, preserved) ─────────────────────────────
        cur.execute("""
            SELECT REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g') AS sc,
                   UPPER(TRIM("Room")) AS room, COUNT(*) AS freq
            FROM historical_data
            WHERE "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
              AND "Room"         IS NOT NULL AND TRIM("Room")         != ''
            GROUP BY REGEXP_REPLACE(UPPER(TRIM("Subject Code")), '[^A-Z0-9]', '', 'g'),
                     UPPER(TRIM("Room"))
        """)
        room_pairs = cur.fetchall() or []

        cur.execute("""
            SELECT UPPER(TRIM("Room")) AS room, COUNT(*) AS total
            FROM historical_data WHERE "Room" IS NOT NULL AND TRIM("Room") != ''
            GROUP BY UPPER(TRIM("Room"))
        """)
        r_totals = {r['room']: int(r['total']) for r in (cur.fetchall() or [])}

        X_r, y_r, rstats = [], [], {}
        for r in room_pairs:
            freq = int(r['freq']); sc = r['sc']; rm = r['room']
            st   = max(s_totals.get(sc, 1), 1)
            rt   = max(r_totals.get(rm, 1), 1)
            feat = [freq, rt, st, freq / st]
            X_r.append(feat)
            y_r.append(_np.log1p(freq))
            rstats[(sc, rm)] = feat

        if len(X_r) >= 5:
            _rf_room_model = _RFR(n_estimators=50, random_state=42)
            _rf_room_model.fit(X_r, y_r)
        _rf_room_stats = rstats

    except Exception:
        pass
    finally:
        cur.close()
        conn.close()


def reset_rf_cache():
    """Discard trained models so the next request triggers a full retrain."""
    global _rf_fac_model, _rf_room_model
    global _rf_fac_stats, _rf_fac_name_stats, _rf_ambiguous_names, _rf_room_stats
    _rf_fac_model       = None
    _rf_room_model      = None
    _rf_fac_stats       = {}
    _rf_fac_name_stats  = {}
    _rf_ambiguous_names = set()
    _rf_room_stats      = {}


# ── Inference & Explainability ────────────────────────────────────────────────

def _lookup_fac_stats(norm_sc: str, employee_number=None, hist_name: str = None,
                      instructor_name: str = None):
    """
    Resolve to a stats dict using this priority order:

    1. (norm_sc, str(employee_number)) in _rf_fac_stats  ← primary; ID-based, no name ambiguity
    2. (norm_sc, UPPER(hist_name))     in _rf_fac_name_stats, only if hist_name is NOT ambiguous
    3. (norm_sc, UPPER(instructor_name)) same condition

    Strategy (1) is always preferred because it uses the Faculty table's unique ID,
    preventing histories of same-surname faculty from being attributed to the wrong person.
    Strategies (2)/(3) apply only when the name is unambiguous (unique surname in Faculty table).
    """
    # 1. Primary: look up by employee number
    if employee_number is not None:
        stats = _rf_fac_stats.get((norm_sc, str(employee_number)))
        if stats:
            return stats

    # 2/3. Fallback: unambiguous name-based keys (different index, different set)
    for name in filter(None, [hist_name, instructor_name]):
        key = name.upper().strip()
        if key in _rf_ambiguous_names:
            continue   # same-surname collision — do not use
        stats = _rf_fac_name_stats.get((norm_sc, key))
        if stats:
            return stats

    return None


def rf_get_faculty_info(
    subject_code: str,
    instructor_name: str,
    hist_name: str = None,
    employee_number=None,
) -> dict:
    """
    Score and explain a faculty candidate for a given subject.

    Parameters
    ----------
    subject_code      Subject being scheduled (any format).
    instructor_name   Name as stored in the Faculty table (display name).
    hist_name         Raw string from historical_data."Instructor" (optional).
    employee_number   Faculty.EmployeeNumber — primary lookup key when provided.

    Returns
    -------
    dict with:
      score          float  RF ranking score (0.0 when not in training data).
      rf_explanation dict   Human-readable breakdown (present only when score > 0).
    """
    result = {'score': 0.0}
    if _rf_fac_model is None:
        return result

    stats = _lookup_fac_stats(
        _norm_sc(subject_code),
        employee_number=employee_number,
        hist_name=hist_name,
        instructor_name=instructor_name,
    )
    if stats is None:
        return result

    result['score'] = float(_rf_fac_model.predict([stats['feat']])[0])

    exact_count  = stats['exact_count']
    spec_ratio   = stats['specialization_ratio']
    recent_count = stats['recent_count']
    latest_ay    = stats['latest_ay']

    parts = [f"Taught {subject_code} {exact_count} time(s) in history."]
    if spec_ratio > 0:
        parts.append(f"{round(spec_ratio * 100, 1)}% of historical assignments are this subject.")
    if recent_count > 0:
        parts.append(f"Taught it {recent_count} time(s) in recent semesters.")
    if latest_ay:
        parts.append(f"Most recent assignment: {latest_ay}.")

    result['rf_explanation'] = {
        'exact_subject_match_count': exact_count,
        'specialization_ratio':      spec_ratio,
        'recent_count':              recent_count,
        'latest_ay':                 latest_ay,
        'explanation':               ' '.join(parts),
    }
    return result


def rf_score_faculty(subject_code: str, instructor_name: str,
                     hist_name: str = None, employee_number=None) -> float:
    """Score only — convenience wrapper around rf_get_faculty_info."""
    return rf_get_faculty_info(subject_code, instructor_name, hist_name, employee_number)['score']


def rf_score_room(subject_code: str, room_name: str) -> float:
    """RF predicted score for a (subject, room) pair; 0.0 if unseen or untrained."""
    if _rf_room_model is None:
        return 0.0
    feat = _rf_room_stats.get((_norm_sc(subject_code), room_name.upper()))
    return float(_rf_room_model.predict([feat])[0]) if feat else 0.0
