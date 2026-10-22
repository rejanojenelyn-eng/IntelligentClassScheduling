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


# Signed-in pages expire a session that has never checked in (app._expire_stale_session
# needs last_seen, which the real login sets). Tests that sign in by writing the session
# directly get the same keys a real login gives them, so they are not logged out.
from flask.testing import FlaskClient as _FlaskClient
from contextlib import contextmanager as _contextmanager

_orig_session_transaction = _FlaskClient.session_transaction


@_contextmanager
def _session_transaction_with_login_clock(self, *args, **kwargs):
    with _orig_session_transaction(self, *args, **kwargs) as sess:
        yield sess
        if sess.get('loggedin'):
            import time, uuid
            now = time.time()
            sess.setdefault('last_seen', now)
            sess.setdefault('last_active', now)
            sess.setdefault('sid', uuid.uuid4().hex)


_FlaskClient.session_transaction = _session_transaction_with_login_clock
