"""
Shared pytest fixtures for the scheduler test suite.

Two kinds of tests live here:
  - Pure unit tests (normalization, CSPValidator rules, _strip_component,
    _find_incomplete_genes) that never touch the database.
  - Real-DB integration tests that exercise the actual production call chain
    (scheduler_engine.generate_draft(), _compute_schedule_evaluation()) against
    the local ASDBv11 Postgres database. These are skipped automatically when
    that database isn't reachable (e.g. CI, a machine without Postgres running)
    instead of failing the whole suite.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _db_reachable() -> bool:
    try:
        import psycopg2
        from config import Config
        conn = psycopg2.connect(
            dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
            host=Config.DB_HOST, port=Config.DB_PORT, connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


DB_AVAILABLE = _db_reachable()

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE, reason="local ASDBv11 Postgres database is not reachable"
)
