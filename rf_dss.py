"""
rf_dss.py — Random Forest Decision Support System (Manual Scheduling)
======================================================================
This module powers the faculty and room suggestions shown to the user
in the Manual Scheduling editor (/api/dss/suggest).

Instead of ranking candidates by raw historical frequency alone, a
RandomForestRegressor learns multi-variate patterns from historical_data:

  Faculty model features : [freq_together, instructor_total, subject_total, ratio]
  Room model features    : [freq_together, room_total,       subject_total, ratio]
  Target                 : log1p(freq_together) — higher = more historically preferred

The models are lazy-trained once per process and cached at module level.
Falls back to frequency-count order when sklearn is not installed.
"""

from database import get_db_connection
from psycopg2.extras import RealDictCursor

try:
    from sklearn.ensemble import RandomForestRegressor as _RFR
    import numpy as _np
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False

_rf_fac_model  = None   # trained RF for faculty ranking (cached)
_rf_room_model = None   # trained RF for room ranking (cached)
_rf_fac_stats  = {}     # (SUBJECT_CODE, INSTRUCTOR) → feature vector
_rf_room_stats = {}     # (SUBJECT_CODE, ROOM_NAME)  → feature vector


def train_rf_dss():
    """
    Build feature matrices from historical_data and fit RF regressors.
    Called once on first DSS request; subsequent calls are no-ops (cached).
    """
    global _rf_fac_model, _rf_room_model, _rf_fac_stats, _rf_room_stats
    if not SKLEARN_OK or _rf_fac_model is not None:
        return

    conn = get_db_connection()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # ── Faculty RF ──────────────────────────────────────────────
        cur.execute("""
            SELECT UPPER(TRIM("Subject Code")) AS sc,
                   UPPER(TRIM("Instructor"))   AS inst,
                   COUNT(*) AS freq
            FROM historical_data
            WHERE "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
              AND "Instructor"   IS NOT NULL AND TRIM("Instructor")   != ''
            GROUP BY UPPER(TRIM("Subject Code")), UPPER(TRIM("Instructor"))
        """)
        fac_pairs = cur.fetchall() or []

        cur.execute("""
            SELECT UPPER(TRIM("Subject Code")) AS sc, COUNT(*) AS total
            FROM historical_data WHERE "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
            GROUP BY UPPER(TRIM("Subject Code"))
        """)
        s_totals = {r['sc']: r['total'] for r in (cur.fetchall() or [])}

        cur.execute("""
            SELECT UPPER(TRIM("Instructor")) AS inst, COUNT(*) AS total
            FROM historical_data WHERE "Instructor" IS NOT NULL AND TRIM("Instructor") != ''
            GROUP BY UPPER(TRIM("Instructor"))
        """)
        i_totals = {r['inst']: r['total'] for r in (cur.fetchall() or [])}

        X_f, y_f, fstats = [], [], {}
        for r in fac_pairs:
            freq = r['freq']; sc = r['sc']; inst = r['inst']
            st = s_totals.get(sc, 1); it = i_totals.get(inst, 1)
            feat = [freq, it, st, freq / st]
            X_f.append(feat); y_f.append(_np.log1p(freq))
            fstats[(sc, inst)] = feat
        if len(X_f) >= 5:
            _rf_fac_model = _RFR(n_estimators=50, random_state=42)
            _rf_fac_model.fit(X_f, y_f)
        _rf_fac_stats = fstats

        # ── Room RF ─────────────────────────────────────────────────
        cur.execute("""
            SELECT UPPER(TRIM("Subject Code")) AS sc,
                   UPPER(TRIM("Room"))         AS room,
                   COUNT(*) AS freq
            FROM historical_data
            WHERE "Subject Code" IS NOT NULL AND TRIM("Subject Code") != ''
              AND "Room"         IS NOT NULL AND TRIM("Room")         != ''
            GROUP BY UPPER(TRIM("Subject Code")), UPPER(TRIM("Room"))
        """)
        room_pairs = cur.fetchall() or []

        cur.execute("""
            SELECT UPPER(TRIM("Room")) AS room, COUNT(*) AS total
            FROM historical_data WHERE "Room" IS NOT NULL AND TRIM("Room") != ''
            GROUP BY UPPER(TRIM("Room"))
        """)
        r_totals = {r['room']: r['total'] for r in (cur.fetchall() or [])}

        X_r, y_r, rstats = [], [], {}
        for r in room_pairs:
            freq = r['freq']; sc = r['sc']; rm = r['room']
            st = s_totals.get(sc, 1); rt = r_totals.get(rm, 1)
            feat = [freq, rt, st, freq / st]
            X_r.append(feat); y_r.append(_np.log1p(freq))
            rstats[(sc, rm)] = feat
        if len(X_r) >= 5:
            _rf_room_model = _RFR(n_estimators=50, random_state=42)
            _rf_room_model.fit(X_r, y_r)
        _rf_room_stats = rstats

    except Exception:
        pass
    finally:
        cur.close(); conn.close()


def rf_score_faculty(subject_code: str, instructor_name: str) -> float:
    """RF predicted score for a (subject, instructor) pair; 0.0 if unseen or untrained."""
    if _rf_fac_model is None:
        return 0.0
    feat = _rf_fac_stats.get((subject_code.upper(), instructor_name.upper()))
    return float(_rf_fac_model.predict([feat])[0]) if feat else 0.0


def rf_score_room(subject_code: str, room_name: str) -> float:
    """RF predicted score for a (subject, room) pair; 0.0 if unseen or untrained."""
    if _rf_room_model is None:
        return 0.0
    feat = _rf_room_stats.get((subject_code.upper(), room_name.upper()))
    return float(_rf_room_model.predict([feat])[0]) if feat else 0.0
