"""
Login → Forgot Password → Employee Number / Username → emailed code → Verify →
New + Confirm Password → Login   (password_reset.py, /forgot-password/*)

Covers: code sent only to the faculty record's email, stored hashed, 10-minute
expiry, single use, resend cooldown, attempt limit, per-IP rate limit, generic
answers for unknown / ineligible accounts, the session-bound reset window (no
changing someone else's password), the password rule, and — most importantly —
that only the password changes: email, contact, photo and
account_setup_complete stay as they were, and the account logs straight in to
its dashboard. Also: Admin Reset Password no longer restarts profile setup.

Uses one real Faculty account (and one Admin); their rows are snapshotted and
restored. Email sending is captured, never really sent.
"""
import itertools
import re

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import requires_db, DB_AVAILABLE

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module
    import password_reset
    import mailer
    from database import query_db
    from config import Config

OLD_PW = 'OldPass#2026'
NEW_PW = 'Fresh2026Pass'
_ACC_COLS = ('passwordhash, password_changed_at, must_change_password, account_setup_complete, '
             'profile_photo, email, pw_otp_verified_at, isactive, role')
_ips = itertools.count(1)


def _acc(userid):
    return query_db(f"SELECT {_ACC_COLS} FROM accounts WHERE userid = %s", [userid], one=True)


@pytest.fixture
def fac(monkeypatch):
    row = query_db("""
        SELECT a.userid, a.username, a.employeenumber, f.email, f.contactnumber
        FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.role = 'Faculty' AND a.isactive AND COALESCE(TRIM(f.email), '') <> ''
          AND a.username <> '13242354'
        ORDER BY a.username LIMIT 1 OFFSET 5""", one=True)
    if not row:
        pytest.skip('needs an active Faculty account with an email')
    snap = dict(_acc(row['userid']))
    query_db("""UPDATE accounts SET passwordhash = %s, password_changed_at = NULL, must_change_password = FALSE,
                account_setup_complete = TRUE, profile_photo = COALESCE(profile_photo, '/static/qa-keep.png')
                WHERE userid = %s RETURNING 1""", [generate_password_hash(OLD_PW), row['userid']])
    before = dict(_acc(row['userid']))

    sent = []
    monkeypatch.setattr(password_reset, 'SEND_IN_BACKGROUND', False)
    monkeypatch.setattr(password_reset, 'send_reset_email', lambda cfg, to, code: sent.append((to, code)))
    try:
        yield {**row, 'sent': sent, 'before': before}
    finally:
        query_db("DELETE FROM password_reset_requests WHERE userid = %s OR request_ip LIKE 'test-%%' RETURNING 1",
                 [row['userid']])
        cols = list(snap.keys())
        query_db(f"UPDATE accounts SET {', '.join(c + ' = %s' for c in cols)} WHERE userid = %s RETURNING 1",
                 [snap[c] for c in cols] + [row['userid']])


def _client():
    """Anonymous browser with its own IP (so the per-IP limit doesn't leak between tests)."""
    return app_module.app.test_client(), {'REMOTE_ADDR': f'test-{next(_ips)}'}


def _request(c, env, identifier):
    return c.post('/forgot-password', data={'identifier': identifier}, environ_base=env)


def _text(r):
    return r.get_data(as_text=True)


# ── happy path ──────────────────────────────────────────────────────────────

def test_full_flow_changes_only_the_password(fac):
    c, env = _client()
    r = _request(c, env, fac['username'])
    assert r.status_code == 302 and r.headers['Location'].endswith('/forgot-password/verify')
    page = _text(c.get('/forgot-password/verify', environ_base=env))
    assert 'we sent a 6-digit verification code' in page

    assert len(fac['sent']) == 1
    to, code = fac['sent'][0]
    assert to == fac['email'].strip() and re.fullmatch(r'\d{6}', code)
    row = query_db("SELECT * FROM password_reset_requests WHERE userid = %s ORDER BY id DESC LIMIT 1",
                   [fac['userid']], one=True)
    assert row['code_hash'] and code not in row['code_hash'] and check_password_hash(row['code_hash'], code)
    assert 590 <= (row['expires_at'] - row['created_at']).total_seconds() <= 610

    r = c.post('/forgot-password/verify', data={'code': code}, environ_base=env)
    assert r.headers['Location'].endswith('/forgot-password/new-password')
    assert 'Create a new password' in _text(c.get('/forgot-password/new-password', environ_base=env))

    r = c.post('/forgot-password/new-password', data={'new_password': NEW_PW, 'confirm_password': NEW_PW},
               environ_base=env)
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert 'Your password has been reset' in _text(c.get('/login', environ_base=env))

    after = _acc(fac['userid'])
    assert check_password_hash(after['passwordhash'], NEW_PW)
    for col in ('account_setup_complete', 'profile_photo', 'email', 'must_change_password', 'isactive', 'role'):
        assert after[col] == fac['before'][col], col
    f = query_db("SELECT email, contactnumber FROM faculty WHERE employeenumber = %s", [fac['employeenumber']], one=True)
    assert (f['email'], f['contactnumber']) == (fac['email'], fac['contactnumber'])
    row = query_db("SELECT * FROM password_reset_requests WHERE id = %s", [row['id']], one=True)
    assert row['used_at'] and row['code_hash'] is None and row['reset_token_hash'] is None

    # straight to the dashboard — no first-time setup
    login = app_module.app.test_client().post('/login', data={'username': fac['username'], 'password': NEW_PW})
    assert login.headers['Location'].endswith('/faculty_dashboard')


def test_employee_number_works_as_identifier(fac):
    c, env = _client()
    _request(c, env, fac['employeenumber'])
    assert len(fac['sent']) == 1


def test_forgot_password_clears_a_pending_required_change_but_not_setup(fac):
    query_db("UPDATE accounts SET must_change_password = TRUE WHERE userid = %s RETURNING 1", [fac['userid']])
    c, env = _client()
    _request(c, env, fac['username'])
    c.post('/forgot-password/verify', data={'code': fac['sent'][0][1]}, environ_base=env)
    c.post('/forgot-password/new-password', data={'new_password': NEW_PW, 'confirm_password': NEW_PW}, environ_base=env)
    after = _acc(fac['userid'])
    assert after['must_change_password'] is False and after['account_setup_complete'] is True


# ── generic answers ─────────────────────────────────────────────────────────

def _flow_messages(identifier, code='000000'):
    c, env = _client()
    r = _request(c, env, identifier)
    page = _text(c.get('/forgot-password/verify', environ_base=env))
    wrong = c.post('/forgot-password/verify', data={'code': code}, environ_base=env)
    return r.status_code, r.headers.get('Location'), password_reset.GENERIC_SENT in page, \
        wrong.status_code, re.search(r'Incorrect verification code\. \d attempts? left\.', _text(wrong)).group(0)


def test_unknown_admin_and_inactive_accounts_look_exactly_like_a_real_one(fac):
    real = _flow_messages(fac['username'])
    assert len(fac['sent']) == 1
    admin = query_db("SELECT username FROM accounts WHERE role = 'Admin' LIMIT 1", one=True)
    for ident in ('NO-SUCH-EMPLOYEE-999', admin['username'] if admin else 'admin'):
        assert _flow_messages(ident) == real, ident
    query_db("UPDATE accounts SET isactive = FALSE WHERE userid = %s RETURNING 1", [fac['userid']])
    assert _flow_messages(fac['username']) == real
    assert len(fac['sent']) == 1                     # nothing sent for any of them


def test_decoy_request_can_never_verify(fac):
    c, env = _client()
    _request(c, env, 'NO-SUCH-EMPLOYEE-999')
    for code in ('000000', '123456'):
        c.post('/forgot-password/verify', data={'code': code}, environ_base=env)
    assert c.get('/forgot-password/new-password', environ_base=env).headers['Location'].endswith('/verify')


# ── code rules ──────────────────────────────────────────────────────────────

def test_code_is_single_use(fac):
    c, env = _client()
    _request(c, env, fac['username'])
    code = fac['sent'][0][1]
    c.post('/forgot-password/verify', data={'code': code}, environ_base=env)
    again = c.post('/forgot-password/verify', data={'code': code}, environ_base=env)
    assert again.headers['Location'].endswith('/forgot-password')      # flow over → start again
    c.post('/forgot-password/new-password', data={'new_password': NEW_PW, 'confirm_password': NEW_PW}, environ_base=env)
    # after the reset the whole flow is closed
    assert c.get('/forgot-password/new-password', environ_base=env).headers['Location'].endswith('/forgot-password')


def test_code_expires(fac):
    c, env = _client()
    _request(c, env, fac['username'])
    query_db("UPDATE password_reset_requests SET expires_at = NOW() - INTERVAL '1 second' WHERE userid = %s RETURNING 1",
             [fac['userid']])
    r = c.post('/forgot-password/verify', data={'code': fac['sent'][0][1]}, environ_base=env)
    assert 'expired' in _text(r)


def test_attempt_limit_then_even_the_right_code_fails(fac):
    c, env = _client()
    _request(c, env, fac['username'])
    code = fac['sent'][0][1]
    wrong = '000000' if code != '000000' else '111111'
    for i in range(password_reset.MAX_ATTEMPTS - 1):
        assert f'{password_reset.MAX_ATTEMPTS - 1 - i} attempt' in _text(
            c.post('/forgot-password/verify', data={'code': wrong}, environ_base=env))
    assert 'Too many incorrect attempts' in _text(c.post('/forgot-password/verify', data={'code': wrong}, environ_base=env))
    assert 'Too many incorrect attempts' in _text(c.post('/forgot-password/verify', data={'code': code}, environ_base=env))


def test_resend_cooldown_and_new_code_replaces_the_old_one(fac):
    c, env = _client()
    _request(c, env, fac['username'])
    old = fac['sent'][0][1]
    r = c.post('/forgot-password/resend', environ_base=env, follow_redirects=True)
    assert 'Please wait' in _text(r) and len(fac['sent']) == 1
    # asking again from the start within the cooldown doesn't send another email either
    _request(c, env, fac['username'])
    assert len(fac['sent']) == 1
    query_db("UPDATE password_reset_requests SET created_at = created_at - INTERVAL '61 seconds' WHERE userid = %s RETURNING 1",
             [fac['userid']])
    c.post('/forgot-password/resend', environ_base=env)
    assert len(fac['sent']) == 2
    new = fac['sent'][1][1]
    if old != new:
        assert 'Incorrect' in _text(c.post('/forgot-password/verify', data={'code': old}, environ_base=env))
    r = c.post('/forgot-password/verify', data={'code': new}, environ_base=env)
    assert r.headers['Location'].endswith('/new-password')


def test_codes_whose_email_failed_are_void_and_do_not_use_up_the_hourly_limit(fac, monkeypatch):
    def boom(cfg, to, code):
        raise mailer.MailError('SMTP error: SMTPAuthenticationError')
    monkeypatch.setattr(password_reset, 'send_reset_email', boom)
    for _ in range(password_reset.MAX_CODES_PER_HOUR + 1):
        c, env = _client()
        _request(c, env, fac['username'])
        query_db("UPDATE password_reset_requests SET created_at = created_at - INTERVAL '61 seconds' "
                 "WHERE userid = %s RETURNING 1", [fac['userid']])
    rows = query_db("SELECT send_failed, code_hash FROM password_reset_requests WHERE userid = %s", [fac['userid']])
    assert rows and all(r['send_failed'] and r['code_hash'] is None for r in rows)
    # email works again → a code is sent even though 6 failed attempts happened this hour
    sent = []
    monkeypatch.setattr(password_reset, 'send_reset_email', lambda cfg, to, code: sent.append(code))
    c, env = _client()
    _request(c, env, fac['username'])
    assert len(sent) == 1
    assert c.post('/forgot-password/verify', data={'code': sent[0]}, environ_base=env).headers['Location'] \
        .endswith('/new-password')


def test_ip_rate_limit(fac):
    c, env = _client()
    for i in range(password_reset.IP_MAX_REQUESTS):
        assert _request(c, env, f'NOBODY-{i}').status_code == 302
    r = _request(c, env, fac['username'])
    assert r.status_code == 429 and 'Too many password reset requests' in _text(r)
    assert not fac['sent']


# ── can't reach or change someone else's password ───────────────────────────

def test_new_password_page_requires_verification(fac):
    c, env = _client()
    assert c.get('/forgot-password/new-password', environ_base=env).headers['Location'].endswith('/forgot-password')
    _request(c, env, fac['username'])
    r = c.post('/forgot-password/new-password', data={'new_password': NEW_PW, 'confirm_password': NEW_PW},
               environ_base=env)
    assert r.headers['Location'].endswith('/verify')
    assert check_password_hash(_acc(fac['userid'])['passwordhash'], OLD_PW)


def test_form_fields_cannot_redirect_the_reset_to_another_account(fac):
    other = query_db("""SELECT userid, username, passwordhash FROM accounts
                        WHERE isactive AND userid <> %s AND role = 'Faculty' LIMIT 1""", [fac['userid']], one=True)
    c, env = _client()
    _request(c, env, fac['username'])
    c.post('/forgot-password/verify', data={'code': fac['sent'][0][1]}, environ_base=env)
    c.post('/forgot-password/new-password', environ_base=env,
           data={'new_password': NEW_PW, 'confirm_password': NEW_PW, 'userid': other['userid'],
                 'username': other['username'], 'employeenumber': other['username']})
    assert query_db("SELECT passwordhash FROM accounts WHERE userid = %s", [other['userid']], one=True)['passwordhash'] \
        == other['passwordhash']
    assert check_password_hash(_acc(fac['userid'])['passwordhash'], NEW_PW)


def test_a_verified_token_only_works_for_its_own_request(fac):
    c, env = _client()
    _request(c, env, fac['username'])
    c.post('/forgot-password/verify', data={'code': fac['sent'][0][1]}, environ_base=env)
    with c.session_transaction() as s:
        s['fp_token'] = 'forged-token'
    assert c.get('/forgot-password/new-password', environ_base=env).headers['Location'].endswith('/verify')


# ── password rule ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('new,confirm,msg', [
    ('Fresh2026Pass', 'Other2026Pass', 'do not match'),
    ('short1', 'short1', 'at least 8'),
    ('onlyletters', 'onlyletters', 'letters and numbers'),
    (OLD_PW, OLD_PW, 'different from your current'),
])
def test_password_rule(fac, new, confirm, msg):
    c, env = _client()
    _request(c, env, fac['username'])
    c.post('/forgot-password/verify', data={'code': fac['sent'][0][1]}, environ_base=env)
    r = c.post('/forgot-password/new-password', data={'new_password': new, 'confirm_password': confirm},
               environ_base=env)
    assert r.status_code == 400 and msg in _text(r)
    assert check_password_hash(_acc(fac['userid'])['passwordhash'], OLD_PW)


# ── separate processes ──────────────────────────────────────────────────────

def test_logged_in_users_are_sent_to_their_dashboard(fac):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=fac['username'], role='Faculty',
                 must_change_password=False, account_setup_complete=True)
    assert c.get('/forgot-password').headers['Location'].endswith('/faculty_dashboard')


def test_admin_reset_forces_only_a_password_change_and_kills_pending_codes(fac):
    c, env = _client()
    _request(c, env, fac['username'])
    admin = app_module.app.test_client()
    with admin.session_transaction() as s:
        s.update(loggedin=True, username='admin', role='Admin', must_change_password=False, account_setup_complete=True)
    admin.post('/admin/accounts/reset_password', data={'user_id': fac['userid'], 'username': fac['username']})
    after = _acc(fac['userid'])
    assert after['must_change_password'] is True and after['account_setup_complete'] is True
    assert 'Incorrect' in _text(c.post('/forgot-password/verify', data={'code': fac['sent'][0][1]}, environ_base=env))

    # temporary password → required password change → dashboard (no profile steps)
    u = app_module.app.test_client()
    r = u.post('/login', data={'username': fac['username'], 'password': f"PUP@{fac['username']}"})
    assert u.get(r.headers['Location']).headers['Location'].endswith('/account-setup/password')
    r = u.post('/account-setup/password', data={'current_password': f"PUP@{fac['username']}",
                                               'new_password': NEW_PW, 'confirm_password': NEW_PW})
    assert r.headers['Location'].endswith('/faculty_dashboard')


# ── email + config ──────────────────────────────────────────────────────────

class _Cfg:
    EMAIL_PROVIDER = ''; EMAIL_FROM = 'sender@example.com'; EMAIL_FROM_NAME = 'PUP'
    BREVO_API_KEY = ''; RESEND_API_KEY = ''; SMTP_HOST = ''; SMTP_FROM = ''
    SMTP_PORT = 587; SMTP_USER = ''; SMTP_PASSWORD = ''; SMTP_USE_SSL = False; SMTP_STARTTLS = True


def test_mailer_picks_a_provider_and_posts_to_the_api(monkeypatch):
    cfg = _Cfg()
    assert mailer.provider(cfg) is None
    cfg.SMTP_HOST = 'smtp.x'; assert mailer.provider(cfg) == 'smtp'
    cfg.RESEND_API_KEY = 're_x'; assert mailer.provider(cfg) == 'resend'
    cfg.BREVO_API_KEY = 'xkeysib'; assert mailer.provider(cfg) == 'brevo'
    cfg.EMAIL_PROVIDER = 'smtp'; assert mailer.provider(cfg) == 'smtp'
    cfg.EMAIL_PROVIDER = ''

    seen = {}
    class _Resp:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout):
        import json
        seen.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data))
        return _Resp()
    monkeypatch.setattr(mailer.urllib.request, 'urlopen', fake_urlopen)
    password_reset.send_reset_email(cfg, 'faculty@example.com', '123456')
    assert seen['url'] == 'https://api.brevo.com/v3/smtp/email'
    assert seen['headers']['Api-key'] == 'xkeysib'
    assert seen['body']['to'] == [{'email': 'faculty@example.com'}] and '123456' in seen['body']['textContent']


def test_no_secrets_in_config_source():
    import pathlib
    src = pathlib.Path(app_module.__file__).with_name('config.py').read_text(encoding='utf-8')
    assert 'rejano' not in src
    assert re.search(r"SECRET_KEY\s*=\s*os\.environ\.get\('SECRET_KEY'\)", src)
