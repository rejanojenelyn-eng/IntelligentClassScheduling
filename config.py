import os

class Config:
    # Database connection details — env vars win in production (e.g. Render),
    # fall back to local dev defaults when not set.
    DB_NAME = os.environ.get("DB_NAME", "ASDBv10")
    DB_USER = os.environ.get("DB_USER", "postgres")
    DB_PASS = os.environ.get("DB_PASS", "rejano24")
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = os.environ.get("DB_PORT", "5432")
    # Neon/Supabase/other hosted Postgres require SSL; local Postgres doesn't.
    DB_SSLMODE = os.environ.get("DB_SSLMODE", "prefer")

    # Secret key for Flask sessions — must be a stable value in production
    # (regenerating it on every restart/worker would invalidate all sessions).
    SECRET_KEY = os.environ.get("SECRET_KEY", "pup_lopez_super_secret_key")
