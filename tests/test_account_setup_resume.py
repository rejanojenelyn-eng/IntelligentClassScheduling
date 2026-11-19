"""
Account Setup must not send teachers back to the start (Nov 2026 reports):
  - a phone that leaves the page for the mail app (no heartbeat) is not signed out
    after 90 seconds while the account is still in setup;
  - the setup progress is kept in account_setup_pending, so an older login cookie
    coming back (a heartbeat answered after the email-code check) loses nothing;
  - signing in again soon continues the setup where it stopped.

Same harness as test_account_setup_review: one connection, rolled back at the end.
"""
import re
import time
import uuid

import pytest

from conftest import requires_db
from test_account_setup_review import env, _set_default_password, _pending_password   # noqa: F401 (fixture)

pytestmark = requires_db


def _choose_new_password(env, new='NewPass456x'):
    _set_default_password(env)
    r = env.client.post('/account-setup/password', data={
        'current_password': 'OldPass123', 'new_password': new, 'confirm_password': new})
    assert r.headers['Location'].endswith('/account-setup/photo')


def _sign_in_again(env, monkeypatch):
    """A new browser session through the real /login route. Only the password / lockout
    check is replaced (it is rate-limited per IP and covered by test_login_lockout)."""
    import app as app_module
    row = app_module.query_db("SELECT * FROM accounts WHERE username = %s", (env.username,), one=True)
    monkeypatch.setattr(app_module._loginsec, 'attempt',
                        lambda conn, username, password, ip: {'result': 'ok', 'user': dict(row)})
    client = app_module.app.test_client()
    r = client.post('/login', data={'username': env.username, 'password': 'OldPass123'})
    assert r.status_code == 302
    return client, r


def _dashboard(env, client):
    with client.session_transaction() as s:
        role = s['role']
    with env.app.app.test_request_context():
        from flask import url_for
        return url_for(env.app._dashboard_url_for_role(role))


# ---- 1. no heartbeat for minutes while in setup (phone in the mail app) -----------------

def test_setup_session_survives_minutes_without_a_heartbeat(env):
    old = time.time() - 5 * 60                      # 5 minutes without any check-in
    with env.client.session_transaction() as s:
        s['last_seen'] = s['last_active'] = old
    r = env.client.get('/account-setup/email')
    assert r.status_code == 200                     # still signed in, same step


def test_finished_account_still_expires_after_90_seconds(env):
    client, _admin = env.admin_client()
    with client.session_transaction() as s:
        s['last_seen'] = s['last_active'] = time.time() - 5 * 60
    r = client.get(_dashboard(env, client))
    assert r.headers['Location'].endswith('/login')


def test_setup_session_still_ends_after_the_setup_limit(env):
    limit = env.app.SETUP_CLOSED_LIMIT.total_seconds()
    with env.client.session_transaction() as s:
        s['last_seen'] = s['last_active'] = time.time() - limit - 60
    r = env.client.get('/account-setup/email')
    assert r.headers['Location'].endswith('/login')


# ---- 2. an older cookie coming back loses nothing ------------------------------------

def test_verified_email_is_kept_when_an_older_cookie_comes_back(env, monkeypatch):
    import email_change
    sent = {}
    monkeypatch.setattr(email_change, 'smtp_configured', lambda cfg: True)
    monkeypatch.setattr(email_change.mailer, 'send_email',
                        lambda cfg, to, subject, text, html=None: sent.update(to=to, text=text))
    address = f"setup.resume.{uuid.uuid4().hex[:10]}@example.com"

    with env.client.session_transaction() as s:     # the cookie as it was before the check
        stale = dict(s)
    r = env.client.post('/api/user/email/request-code', data={'email': address})
    assert r.get_json()['success'], r.get_json()
    code = re.search(r'\b(\d{6})\b', sent['text']).group(1)
    r = env.client.post('/api/user/email/update', data={'email': address, 'code': code})
    assert r.get_json()['success'], r.get_json()

    with env.client.session_transaction() as s:     # a heartbeat answered later puts it back
        s.clear(); s.update(stale)
    r = env.client.post('/account-setup/email', data={'action': 'continue', 'email': address})
    assert r.headers['Location'].endswith('/account-setup/contact')    # not "please verify again"
    page = env.client.get('/account-setup/verify').data
    assert address.encode() in page and b'Verified' in page


def test_new_password_is_kept_when_an_older_cookie_comes_back(env):
    _set_default_password(env)
    with env.client.session_transaction() as s:
        stale = dict(s)
    r = env.client.post('/account-setup/password', data={
        'current_password': 'OldPass123', 'new_password': 'NewPass456x', 'confirm_password': 'NewPass456x'})
    assert r.headers['Location'].endswith('/account-setup/photo')
    with env.client.session_transaction() as s:     # setup_password_set lost from the cookie
        s.clear(); s.update(stale)
    r = env.client.get('/account-setup/email')
    assert r.status_code == 200                     # not sent back to step 1


# ---- 3. signing in again continues the setup ----------------------------------------

def test_signing_in_again_continues_where_setup_stopped(env, monkeypatch):
    from werkzeug.security import check_password_hash
    _choose_new_password(env)
    assert env.client.get('/account-setup/email').status_code == 200   # the step reached

    client, r = _sign_in_again(env, monkeypatch)                         # session had ended
    dash = client.get(r.headers['Location'])
    assert dash.headers['Location'].endswith('/account-setup/email')     # not back to step 1
    assert b'New password set' in client.get('/account-setup/password').data
    assert env.q("SELECT details FROM activity_log WHERE initiated_by = %s ORDER BY logid DESC LIMIT 1",
                 (env.username,))[0][0].endswith('continued an unfinished Account Setup')

    r = client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done')
    hash_ = env.q("SELECT passwordhash FROM accounts WHERE username = %s", (env.username,))[0][0]
    assert check_password_hash(hash_, 'NewPass456x')                     # the password chosen before
    assert not _pending_password(env)


def test_an_old_unfinished_setup_starts_over(env, monkeypatch):
    _choose_new_password(env)
    env.client.get('/account-setup/email')
    env.q("UPDATE account_setup_pending SET updated_at = NOW() - INTERVAL '3 hours', "
          "created_at = NOW() - INTERVAL '3 hours' WHERE username = %s RETURNING 1", (env.username,))
    client, r = _sign_in_again(env, monkeypatch)
    dash = client.get(r.headers['Location'])
    assert dash.headers['Location'].endswith('/account-setup/password')
    assert not _pending_password(env)


def test_admin_password_reset_forgets_a_password_chosen_in_setup(env):
    _choose_new_password(env)
    assert _pending_password(env)[0][0]
    userid = env.q("SELECT userid FROM accounts WHERE username = %s", (env.username,))[0][0]
    cur = env.shared.cursor()
    env.app._reset_to_temporary_password(cur, userid, env.username)
    cur.close()
    assert _pending_password(env)[0][0] is None                          # must choose again
