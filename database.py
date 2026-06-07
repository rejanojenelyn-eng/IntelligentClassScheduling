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
    # Using RealDictCursor allows you to access columns by name: result['EmployeeNumber']
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, args)
    rv = cur.fetchall()
    conn.commit()
    cur.close()
    conn.close()
    return (rv[0] if rv else None) if one else rv

_SCHEDULER_CONFIG_DEFAULTS = {
    'sc1_daytime':    20,
    'sc2_night':      15,
    'sc3_day_dist':   10,
    'sc4_compact':    10,
    'sc5_pt_balance': 10,
    'sc6_weekend':    10,
    'sc7_consecutive':30,
    'hc7_max_night':   2,
}

def load_scheduler_config() -> dict:
    """Return scheduler penalty weights from DB, falling back to defaults."""
    try:
        rows = query_db("SELECT config_key, config_value FROM scheduler_config")
        cfg = dict(_SCHEDULER_CONFIG_DEFAULTS)
        for row in rows:
            key = row['config_key']
            if key in cfg:
                cfg[key] = float(row['config_value'])
        return cfg
    except Exception:
        return dict(_SCHEDULER_CONFIG_DEFAULTS)