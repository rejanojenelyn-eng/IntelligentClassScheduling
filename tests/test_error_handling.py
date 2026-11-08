"""
Error handling: no database errors, stack traces or internal details reach users.

  * app._public_error(e): the app's own validation messages (ValueError, workflow errors)
    pass through; psycopg2 errors, programming errors and library errors are logged
    server-side with a reference id and replaced by a generic message.
  * Every place a view put str(e) / {e} into a response now goes through _public_error
    (checked over app.py's syntax tree, so a new leak fails this file).
  * Exceptions a view does not catch get a generic 500 (app._unhandled_error), JSON or HTML.
  * The Werkzeug debugger is off unless FLASK_DEBUG=1.
"""
import ast
import time
import uuid
from pathlib import Path

import psycopg2
import psycopg2.errors
import pytest

import app as app_module

FETCH = {'Sec-Fetch-Mode': 'cors'}
PAGE = {'Sec-Fetch-Mode': 'navigate'}
APP_PY = Path(__file__).resolve().parent.parent / 'app.py'
SECRET_SQL = ('relation "public.merge_load_policy" does not exist\n'
              'LINE 1: DELETE FROM public.merge_load_policy WHERE policyid=1')
LEAK_MARKERS = ('merge_load_policy', 'LINE 1', 'relation', 'psycopg2', 'Traceback', 'File "', 'app.py',
                'UndefinedTable', 'DELETE FROM')


def _admin_client(monkeypatch):
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    monkeypatch.setattr(app_module, '_sweep_expired_makeups', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_runtime_schema_ready', lambda: True)
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Admin', username='err-test', sid=uuid.uuid4().hex,
                 last_seen=time.time(), last_active=time.time(),
                 must_change_password=False, account_setup_complete=True)
    return c


class _FailingCursor:
    def __init__(self, exc):
        self.exc = exc

    def execute(self, *a, **k):
        raise self.exc

    def close(self):
        pass


class _FailingConn:
    def __init__(self, exc):
        self.exc = exc

    def cursor(self, *a, **k):
        return _FailingCursor(self.exc)

    def rollback(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


# ── _public_error ────────────────────────────────────────────────────────────

def test_database_errors_are_replaced_by_a_generic_message_with_a_reference(capsys):
    msg = app_module._public_error(psycopg2.errors.UndefinedTable(SECRET_SQL))
    assert msg.startswith(app_module.GENERIC_ERROR_MESSAGE) and 'Reference:' in msg
    assert not any(m in msg for m in LEAK_MARKERS)
    ref = msg.split('Reference: ')[1].rstrip(')')
    logged = capsys.readouterr().out
    assert ref in logged and 'merge_load_policy' in logged          # the detail is in the server log


@pytest.mark.parametrize('exc', [KeyError('passwordhash'), TypeError("'NoneType' object is not subscriptable"),
                                 AttributeError("'dict' object has no attribute 'roomid'"),
                                 psycopg2.OperationalError('could not connect to server at 10.0.0.5:5432'),
                                 RuntimeError('C:\\Users\\ryanv\\Desktop\\app.py line 42'),
                                 UnicodeDecodeError('utf-8', b'\x89', 0, 1, 'invalid start byte')])
def test_internal_errors_are_hidden(exc):
    msg = app_module._public_error(exc)
    assert msg.startswith(app_module.GENERIC_ERROR_MESSAGE), msg
    assert str(exc) not in msg


def test_the_apps_own_validation_messages_still_reach_the_user():
    assert app_module._public_error(ValueError('Year level must be between 1 and 5.')) == \
        'Year level must be between 1 and 5.'


# ── real endpoint, real exception ─────────────────────────────────────────────

def test_endpoint_hit_by_a_database_error_reveals_nothing(monkeypatch):
    client = _admin_client(monkeypatch)
    monkeypatch.setattr(app_module, 'get_db_connection',
                        lambda: _FailingConn(psycopg2.errors.UndefinedTable(SECRET_SQL)))
    r = client.delete('/admin/settings/merge_load_policies/1', headers=FETCH)
    body = r.get_data(as_text=True)
    assert r.status_code == 500 and r.get_json()['success'] is False
    assert 'Reference:' in body and not any(m in body for m in LEAK_MARKERS), body


@pytest.mark.parametrize('headers,kind', [(FETCH, 'application/json'), (PAGE, 'text/html')])
def test_uncaught_exception_gets_a_generic_500(monkeypatch, headers, kind):
    client = _admin_client(monkeypatch)

    def _boom(**kw):
        raise RuntimeError('secret internal detail: password=hunter2 at C:\\app.py line 42')
    monkeypatch.setitem(app_module.app.view_functions, 'api_list_merge_load_policies', _boom)
    r = client.get('/admin/settings/merge_load_policies', headers=headers)
    body = r.get_data(as_text=True)
    assert r.status_code == 500 and r.mimetype == kind
    assert 'Something went wrong' in body and 'Reference:' in body
    assert 'hunter2' not in body and 'Traceback' not in body and 'line 42' not in body


def test_http_errors_keep_their_status():
    c = app_module.app.test_client()
    assert c.get('/definitely-not-a-page').status_code == 404
    assert c.get('/api/session/page_closed').status_code == 405


# ── no leak is left in the code ───────────────────────────────────────────────

_LOG_CALLS = {'print', 'format_exc', 'print_exc', 'exception', 'error', 'warning', 'debug', 'info'}


def _leaks():
    """str(e) / {e} inside `except ... as e:` used anywhere except logging, raise, a
    comparison or a method call on the text (those never reach the user)."""
    tree = ast.parse(APP_PY.read_text(encoding='utf-8'))
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            c._p = n
    out = []
    for h in ast.walk(tree):
        if not (isinstance(h, ast.ExceptHandler) and h.name):
            continue
        for node in (x for s in h.body for x in ast.walk(s)):
            hit = ((isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'str'
                    and len(node.args) == 1 and getattr(node.args[0], 'id', '') == h.name)
                   or (isinstance(node, ast.FormattedValue) and getattr(node.value, 'id', '') == h.name))
            if not hit:
                continue
            p, child, ok = getattr(node, '_p', None), node, False
            while p is not None and not isinstance(p, (ast.FunctionDef, ast.ExceptHandler)):
                name = getattr(getattr(p, 'func', None), 'id', None) or getattr(getattr(p, 'func', None), 'attr', None)
                if (isinstance(p, (ast.Raise, ast.Compare)) or (isinstance(p, ast.Call) and name in _LOG_CALLS)
                        or (isinstance(p, ast.Attribute) and p.value is child)):
                    ok = True
                    break
                child, p = p, getattr(p, '_p', None)
            if not ok:
                out.append(node.lineno)
    return out


def test_no_exception_text_is_put_into_a_response():
    assert _leaks() == [], 'app.py lines still sending exception text to users: use _public_error(e)'


def test_no_traceback_or_database_diagnostics_in_responses():
    src = APP_PY.read_text(encoding='utf-8')
    assert "'traceback': traceback.format_exc()" not in src
    assert 'diag.message_primary}' not in src


def test_debugger_is_off_by_default():
    src = APP_PY.read_text(encoding='utf-8')
    assert 'debug=True' not in src
    assert "os.environ.get('FLASK_DEBUG', '0')" in src
    assert app_module.app.debug is False
