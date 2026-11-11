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
            import app as _app_module             # a real login is tracked as signed in since this run
            if _app_module._login_unknown(sess['sid']):
                _app_module._track_login(sess['sid'])


_FlaskClient.session_transaction = _session_transaction_with_login_clock


# CSRF protection (app._csrf_protect) stays ON in the test suite. Like the page's csrf.js,
# the test client sends this session's token with every POST/PUT/PATCH/DELETE. A test that
# checks what happens WITHOUT a token sets client.csrf_auto = False.
_orig_open = _FlaskClient.open
_UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


def _open_with_csrf_token(self, *args, **kwargs):
    method = str(kwargs.get('method') or 'GET').upper()
    if method in _UNSAFE_METHODS and getattr(self, 'csrf_auto', True) and self._cookies is not None:
        from werkzeug.datastructures import Headers
        headers = Headers(kwargs.get('headers') or {})
        if 'X-CSRFToken' not in headers:
            import uuid
            with self.session_transaction() as sess:
                token = sess.setdefault('_csrf_token', 'test-csrf-' + uuid.uuid4().hex)
            headers['X-CSRFToken'] = token
            kwargs['headers'] = headers
    return _orig_open(self, *args, **kwargs)


_FlaskClient.open = _open_with_csrf_token


def signed_in_client(role='Academic Head'):
    """A test client signed in as a real, active account of this role. Every endpoint
    except the public ones needs a login (app._require_login) and most need a specific
    role (app.roles_required), and each request re-checks that the account is active,
    so the account must exist in the database."""
    import app as _app_module
    acc = _app_module.query_db("SELECT userid, username FROM accounts WHERE isactive AND role = %s "
                               "ORDER BY userid LIMIT 1", (role,), one=True)
    if not acc:
        pytest.skip(f'no active {role} account in the database')
    client = _app_module.app.test_client()
    with client.session_transaction() as sess:
        sess.update(loggedin=True, role=role, username=acc['username'], userid=acc['userid'],
                    must_change_password=False, account_setup_complete=True)
    return client


# Faculty requests (/api/faculty/submit_request) require the students' signed consent
# letter: the fields go as JSON in `payload`, with the file as `attachment`. Use as
#   client.post('/api/faculty/submit_request', **with_consent_letter({...}))
_CONSENT_PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


def with_consent_letter(payload, filename='consent_letter.pdf', content=_CONSENT_PDF):
    import io as _io
    import json as _json
    return {'data': {'payload': _json.dumps(payload), 'attachment': (_io.BytesIO(content), filename)},
            'content_type': 'multipart/form-data'}
