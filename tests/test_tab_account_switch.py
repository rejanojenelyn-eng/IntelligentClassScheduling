"""
All tabs of one browser share ONE login cookie, so a sign-in on another tab turns an
open tab into that account. Each signed-in page says who it was opened as (the session
guard's data-user) and the heartbeat says who the browser is signed in as now, so
session_guard.js can cover a stale tab instead of letting it act as the wrong user.
Also: the login cookie's hardening flags.
"""
import time
import uuid
from pathlib import Path

import pytest

import app as app_module
from conftest import requires_db

GUARD_JS = (Path(__file__).resolve().parents[1] / 'static' / 'js' / 'session_guard.js').read_text(encoding='utf-8')


def _account(role):
    row = app_module.query_db("SELECT userid, username, role FROM accounts WHERE isactive AND role = %s "
                              "ORDER BY userid LIMIT 1", [role], one=True)
    if not row:
        pytest.skip(f'no active {role} account')
    return row


def _client(acct):
    c, sid = app_module.app.test_client(), uuid.uuid4().hex
    app_module._track_login(sid)
    now = time.time()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=acct['username'], userid=acct['userid'], role=acct['role'], sid=sid, last_seen=now, last_active=now,
                 must_change_password=False, account_setup_complete=True)
    return c


@requires_db
def test_signed_in_page_says_who_it_was_opened_as():
    acct = _account('Academic Head')
    page = _client(acct).get('/').get_data(as_text=True)
    assert 'session_guard.js' in page
    assert f'data-user="{acct["userid"]}"' in page and 'data-role="Academic Head"' in page


@requires_db
def test_heartbeat_reports_the_current_account():
    acct = _account('Faculty')
    body = _client(acct).post('/api/session/heartbeat', json={'page': 'p1'}).get_json()
    assert body['success'] and body['user'] == str(acct['userid']) and body['role'] == 'Faculty'
    assert body['name']


def test_guard_blocks_a_tab_whose_account_changed():
    assert 'dss_login_account' in GUARD_JS              # tabs tell each other (storage event)
    assert "addEventListener('storage'" in GUARD_JS
    assert 'accountChanged(d)' in GUARD_JS              # and the heartbeat double-checks


def test_login_cookie_is_hardened():
    cfg = app_module.app.config
    assert cfg['SESSION_COOKIE_HTTPONLY'] is True
    assert cfg['SESSION_COOKIE_SAMESITE'] == 'Lax'
    assert cfg['SESSION_COOKIE_SECURE'] == app_module.Config.SESSION_COOKIE_SECURE
