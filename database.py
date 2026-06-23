import psycopg2
from psycopg2.extras import RealDictCursor
from config import Config

def get_db_connection():
    try:
        conn = psycopg2.connect(
            dbname=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASS,
            host=Config.DB_HOST,
            port=Config.DB_PORT
        )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

def query_db(query, args=(), one=False):
    """Function to run queries and return results as dictionaries"""
    conn = get_db_connection()
    if conn is None:
        raise RuntimeError(
            f"Could not connect to database '{Config.DB_NAME}' at "
            f"{Config.DB_HOST}:{Config.DB_PORT}. "
            "Check that PostgreSQL is running and the database exists."
        )
    # Using RealDictCursor allows you to access columns by name: result['EmployeeNumber']
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, args)
    rv = cur.fetchall()
    conn.commit()
    cur.close()
    conn.close()
    return (rv[0] if rv else None) if one else rv

import json as _json

_SCHEDULER_CONFIG_DEFAULTS = {
    # ── Soft constraint penalty weights ────────────────────
    'sc1_daytime':    20,
    'sc2_night':      15,
    'sc3_day_dist':   10,
    'sc4_compact':    10,
    'sc5_pt_balance': 10,
    'sc6_weekend':    10,
    'sc7_consecutive':30,
    # ── Hard constraint limits ──────────────────────────────
    'hc7_max_night':   2,
    # ── HC toggle switches (1 = enabled, 0 = disabled) ─────
    'hc_weekend_enabled':          1,
    'hc_day_pairing_enabled':      1,
    'hc_time_blocks_enabled':      1,
    'hc_faculty_load_enabled':     1,
    'hc_room_conflict_enabled':    1,
    'hc_faculty_conflict_enabled': 1,
    'hc_section_conflict_enabled': 1,
    'hc_lab_session_enabled':      1,
    'hc_program_restrict_enabled': 1,
    'hc_publish_gate_enabled':     1,
    'hc_faculty_spec_enabled':     1,
    # ── HC configurable params (stored as JSON strings) ─────
    'hc_day_pairs':
        '[["Monday","Thursday"],["Tuesday","Friday"],["Wednesday","Saturday"]]',
    'hc_time_slots':
        '[[7,30,9,0],[9,0,10,30],[10,30,12,0],[12,0,13,30],[13,30,15,0],'
        '[15,0,16,30],[16,30,18,0],[18,0,19,30],[19,30,21,0],'
        '[7,30,9,30],[9,0,11,0],[10,30,12,30],[12,0,14,0],[13,30,15,30],[14,30,16,30],'
        '[16,30,18,30],[18,0,20,0],[19,0,21,0],'
        '[10,30,13,30],[13,30,16,30],[7,30,10,30],[9,0,12,0],[16,30,19,30],[18,0,21,0]]',
    'hc_weekend_subject': 'nstp_only',
    'hc_weekend_day':     'sunday_only',
}

# Keys whose DB values should stay as strings (not converted to float)
_STRING_KEYS = {'hc_day_pairs', 'hc_time_slots', 'hc_weekend_subject', 'hc_weekend_day'}

def load_scheduler_config() -> dict:
    """Return scheduler config from DB, merged with defaults.
    Numeric keys are returned as float; string/JSON keys as str."""
    try:
        rows = query_db("SELECT config_key, config_value FROM scheduler_config")
        cfg  = dict(_SCHEDULER_CONFIG_DEFAULTS)
        for row in rows:
            key = row['config_key']
            val = row['config_value']
            if val is None:
                continue
            if key in _STRING_KEYS:
                cfg[key] = val          # keep as string (JSON or plain text)
            else:
                try:
                    cfg[key] = float(val)
                except (TypeError, ValueError):
                    cfg[key] = val
        return cfg
    except Exception:
        return dict(_SCHEDULER_CONFIG_DEFAULTS)