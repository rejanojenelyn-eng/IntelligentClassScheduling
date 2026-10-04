"""
Profile Settings → Change Password, end to end through the Flask routes
against the local ASDBv11 database:

  eligibility (once per calendar month) → emailed code (hashed, 10 min,
  single use, 5 attempts, 60 s resend) → verify → current/new/confirm →
  password + password_changed_at updated.

Also covers the Back-button fix: GET /login with a live session goes to the
user's dashboard instead of ending the session.

Uses one real Faculty account; its accounts row is snapshotted before each
test and restored afterwards. Email sending is replaced by a capture.
"""
from datetime import datetime

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from conftest import requires_db, DB_AVAILABLE

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module
    import password_change
    from database import query_db
    from config import Config

OLD_PW = 'OldPass#2026'
NEW_PW = 'NewPass#2026'
_COLS = ('passwordhash, password_changed_at, email, pw_otp_hash, pw_otp_expires_at, '
         'pw_otp_sent_at, pw_otp_attempts, pw_otp_verified_at, must_change_password, account_setup_complete')


def _acc(username):
    return query_db(f"SELECT {_COLS} FROM accounts WHERE username = %s", [username], one=True)


@pytest.fixture
def user(monkeypatch):
    row = query_db("""
        SELECT a.username FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.role = 'Faculty' AND a.isactive AND COALESCE(f.email, '') <> ''
        ORDER BY a.username LIMIT 1 OFFSET 3""", one=True)
    other = query_db("""SELECT username FROM accounts WHERE role = 'Faculty' AND isactive AND username <> %s
                        ORDER BY username LIMIT 1""", [row['username']], one=True)['username']
    snap = {u: dict(_acc(u)) for u in (row['username'], other)}
    query_db(f"""UPDATE accounts SET passwordhash = %s, password_changed_at = NULL, pw_otp_hash = NULL,
                 pw_otp_expires_at = NULL, pw_otp_sent_at = NULL, pw_otp_attempts = 0,
                 pw_otp_verified_at = NULL, must_change_password = FALSE, account_setup_complete = TRUE
                 WHERE username = %s RETURNING 1""", [generate_password_hash(OLD_PW), row['username']])

    sent = []
    monkeypatch.setattr(password_change, 'send_code_email', lambda cfg, to, code: sent.append((to, code)))
    monkeypatch.setattr(Config, 'SMTP_HOST', 'smtp.test', raising=False)
    monkeypatch.setattr(Config, 'SMTP_FROM', 'noreply@test', raising=False)
    try:
        yield {'username': row['username'], 'other': other, 'sent': sent}
    finally:
        for u, s in snap.items():
            cols = list(s.keys())
            query_db(f"UPDATE accounts SET {', '.join(c + ' = %s' for c in cols)} WHERE username = %s RETURNING 1",
                     [s[c] for c in cols] + [u])
        query_db("DELETE FROM activity_log WHERE action = 'Password Changed' AND details LIKE %s RETURNING 1",
                 [row['username'] + ' %'])


def _client(username, role='Faculty'):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=username, role=role,
                 must_change_password=False, account_setup_complete=True)
    return c


def _request_code(c, u):
    r = c.post('/api/user/password/request-code')
    assert r.status_code == 200, r.get_json()
    return u['sent'][-1][1]


def _verified_client(u):
    c = _client(u['username'])
    code = _request_code(c, u)
    assert c.post('/api/user/password/verify-code', data={'code': code}).status_code == 200
    return c


def _change(c, current=OLD_PW, new=NEW_PW, confirm=None, **extra):
    return c.post('/api/user/password/change', data={
        'current_password': current, 'new_password': new,
        'confirm_password': new if confirm is None else confirm, **extra})


# ── eligibility & sending ───────────────────────────────────────────────────

def test_fresh_account_is_eligible_and_email_is_masked(user):
    d = _client(user['username']).get('/api/user/password/eligibility').get_json()
    assert d['success'] and d['eligible'] and d['has_email']
    assert '*' in d['email_masked'] and '@' in d['email_masked']


def test_code_is_emailed_hashed_and_never_returned(user, capsys):
    c = _client(user['username'])
    r = c.post('/api/user/password/request-code')
    body = r.get_json()
    to, code = user['sent'][-1]
    assert r.status_code == 200 and len(code) == 6 and code.isdigit()
    assert code not in r.get_data(as_text=True)
    assert body['expires_in_minutes'] == 10
    row = _acc(user['username'])
    assert row['pw_otp_hash'] and code not in row['pw_otp_hash']
    assert check_password_hash(row['pw_otp_hash'], code)
    assert (row['pw_otp_expires_at'] - row['pw_otp_sent_at']).total_seconds() == 600
    assert code not in capsys.readouterr().out


def test_resend_is_rate_limited(user):
    c = _client(user['username'])
    _request_code(c, user)
    r = c.post('/api/user/password/request-code')
    assert r.status_code == 429 and r.get_json()['error'] == 'too_soon'
    assert len(user['sent']) == 1


def test_unconfigured_or_failing_email_stores_no_code(user, monkeypatch):
    c = _client(user['username'])
    monkeypatch.setattr(Config, 'SMTP_HOST', '')
    r = c.post('/api/user/password/request-code')
    assert r.status_code == 503 and _acc(user['username'])['pw_otp_hash'] is None
    monkeypatch.setattr(Config, 'SMTP_HOST', 'smtp.test')

    def boom(*a):
        raise OSError('smtp down')
    monkeypatch.setattr(password_change, 'send_code_email', boom)
    r = c.post('/api/user/password/request-code')
    assert r.status_code == 502 and 'smtp down' not in r.get_data(as_text=True)
    assert _acc(user['username'])['pw_otp_hash'] is None


def test_account_without_email_is_told_to_add_one(user):
    row = query_db("""SELECT a.username FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
                      WHERE a.isactive AND COALESCE(TRIM(f.email), '') = '' LIMIT 1""", one=True)
    if not row:
        pytest.skip('every account has an email')
    c = _client(row['username'])
    assert c.get('/api/user/password/eligibility').get_json()['has_email'] is False
    r = c.post('/api/user/password/request-code')
    assert r.status_code == 400 and r.get_json()['error'] == 'no_email'
    assert _acc(row['username'])['pw_otp_hash'] is None


# ── standalone system Admin (no faculty record) ─────────────────────────────

ADMIN_TEST_EMAIL = 'system.admin@pup-lopez.test'


@pytest.fixture
def admin(user):
    """The real 'admin' account with a temporary test email; restored afterwards.
    Also snapshots every faculty row so tests can prove none was touched."""
    snap = dict(query_db(f"SELECT employeenumber, {_COLS} FROM accounts WHERE username = 'admin'", one=True))
    faculty_before = [dict(r) for r in query_db("SELECT * FROM faculty ORDER BY employeenumber")]
    query_db("UPDATE accounts SET email = %s WHERE username = 'admin' RETURNING 1", [ADMIN_TEST_EMAIL])
    try:
        yield {'faculty_before': faculty_before}
    finally:
        query_db(f"UPDATE accounts SET {', '.join(k + ' = %s' for k in snap)} WHERE username = 'admin' RETURNING 1",
                 list(snap.values()))


def _admin_client():
    return _client('admin', 'Admin')


def test_admin_is_a_standalone_account(admin):
    row = query_db("SELECT employeenumber, role FROM accounts WHERE username = 'admin'", one=True)
    assert row['role'] == 'Admin' and row['employeenumber'] is None


def test_admin_code_goes_to_the_admin_email(user, admin):
    r = _admin_client().post('/api/user/password/request-code')
    assert r.status_code == 200, r.get_json()
    assert user['sent'][-1][0] == ADMIN_TEST_EMAIL
    assert r.get_json()['email_masked'].endswith('@pup-lopez.test')


def test_admin_without_email_is_told_to_add_one(user, admin):
    query_db("UPDATE accounts SET email = NULL WHERE username = 'admin' RETURNING 1")
    c = _admin_client()
    assert c.get('/api/user/password/eligibility').get_json()['has_email'] is False
    assert c.post('/api/user/password/request-code').get_json()['error'] == 'no_email'


def test_admin_email_is_saved_on_the_account_and_no_faculty_row_changes(user, admin):
    r = _admin_client().post('/api/user/email/update', data={'email': 'new.admin@pup-lopez.test'})
    assert r.status_code == 200, r.get_json()
    assert query_db("SELECT email FROM accounts WHERE username = 'admin'", one=True)['email'] == 'new.admin@pup-lopez.test'
    assert [dict(r) for r in query_db("SELECT * FROM faculty ORDER BY employeenumber")] == admin['faculty_before']


def test_admin_displays_as_administrator(admin):
    c = _admin_client()
    me = c.get('/api/user/me').get_json()
    assert me['fullname'] == 'Administrator' and me['emp_num'] == '' and me['email'] == ADMIN_TEST_EMAIL
    html = c.get('/admin/dashboard').get_data(as_text=True)
    assert '<span class="user-name">Administrator</span>' in html
    assert 'REJANO' not in html.upper().split('<SPAN CLASS="USER-NAME">')[1].split('</SPAN>')[0]


def test_faculty_code_uses_faculty_email_even_if_accounts_email_is_set(user):
    fac_email = query_db("""SELECT f.email FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
                            WHERE a.username = %s""", [user['username']], one=True)['email']
    query_db("UPDATE accounts SET email = 'decoy@should-not-be-used.test' WHERE username = %s RETURNING 1",
             [user['username']])
    r = _client(user['username']).post('/api/user/password/request-code')
    assert r.status_code == 200 and user['sent'][-1][0] == fac_email


def test_faculty_record_13242354_is_separate_from_admin():
    fac = query_db("SELECT firstname, lastname, email FROM faculty WHERE employeenumber = '13242354'", one=True)
    if not fac:
        pytest.skip('faculty record 13242354 not present')
    assert (fac['firstname'], fac['lastname'], fac['email']) == ('Jenelyn', 'REJANO', 'rejanojenelyn1226@gmail.com')
    assert query_db("SELECT username FROM accounts WHERE employeenumber = '13242354'") == []


# ── verification ────────────────────────────────────────────────────────────

def test_wrong_codes_are_limited_to_five(user):
    c = _client(user['username'])
    code = _request_code(c, user)
    wrong = '000000' if code != '000000' else '111111'
    for left in (4, 3, 2, 1):
        d = c.post('/api/user/password/verify-code', data={'code': wrong}).get_json()
        assert d['error'] == 'invalid_code' and d['attempts_left'] == left
    d = c.post('/api/user/password/verify-code', data={'code': wrong}).get_json()
    assert d['error'] == 'too_many_attempts'
    # the real code is now dead too
    assert c.post('/api/user/password/verify-code', data={'code': code}).get_json()['error'] == 'no_code'


def test_expired_code_is_rejected(user):
    c = _client(user['username'])
    code = _request_code(c, user)
    query_db("UPDATE accounts SET pw_otp_expires_at = NOW() - INTERVAL '1 second' WHERE username = %s RETURNING 1",
             [user['username']])
    r = c.post('/api/user/password/verify-code', data={'code': code})
    assert r.status_code == 400 and r.get_json()['error'] == 'code_expired'
    assert _change(c).status_code == 403


def test_code_is_single_use(user):
    c = _client(user['username'])
    code = _request_code(c, user)
    assert c.post('/api/user/password/verify-code', data={'code': code}).status_code == 200
    assert _acc(user['username'])['pw_otp_hash'] is None
    assert c.post('/api/user/password/verify-code', data={'code': code}).get_json()['error'] == 'no_code'


def test_cannot_change_without_verifying(user):
    r = _change(_client(user['username']))
    assert r.status_code == 403 and r.get_json()['error'] == 'not_verified'
    assert check_password_hash(_acc(user['username'])['passwordhash'], OLD_PW)


def test_verification_is_bound_to_the_session_that_verified(user):
    _verified_client(user)
    other_browser = _client(user['username'])
    assert _change(other_browser).status_code == 403


def test_verified_window_expires(user):
    c = _verified_client(user)
    row = query_db("UPDATE accounts SET pw_otp_verified_at = pw_otp_verified_at - INTERVAL '11 minutes' "
                   "WHERE username = %s RETURNING pw_otp_verified_at", [user['username']], one=True)
    # keep the session bound to the (shifted) verification so only the age is wrong
    with c.session_transaction() as s:
        s['pw_otp_verified_at'] = row['pw_otp_verified_at'].isoformat()
    r = _change(c)
    assert r.status_code == 403 and r.get_json()['error'] == 'not_verified'
    query_db("UPDATE accounts SET pw_otp_verified_at = pw_otp_verified_at + INTERVAL '2 minutes' "
             "WHERE username = %s RETURNING 1", [user['username']])      # 9 min old → still valid
    with c.session_transaction() as s:
        s['pw_otp_verified_at'] = _acc(user['username'])['pw_otp_verified_at'].isoformat()
    assert _change(c).status_code == 200


# ── new password validation ────────────────────────────────────────────────

@pytest.mark.parametrize('kwargs,fragment', [
    ({'current': 'wrong-password'}, 'Current password is incorrect'),
    ({'confirm': 'Different#1'}, 'do not match'),
    ({'new': 'abc'}, 'at least 8'),
    ({'new': 'lettersonly'}, 'letters and numbers'),
    ({'new': 'password123'}, 'too common'),
    ({'new': OLD_PW}, 'different from your current'),
    ({'current': ''}, 'fill in all'),
])
def test_invalid_new_password_is_rejected_and_nothing_changes(user, kwargs, fragment):
    c = _verified_client(user)
    r = _change(c, **kwargs)
    assert r.status_code == 400 and fragment in r.get_json()['message']
    row = _acc(user['username'])
    assert check_password_hash(row['passwordhash'], OLD_PW) and row['password_changed_at'] is None
    # a typo doesn't burn the verification
    assert _change(c).status_code == 200


def test_five_wrong_current_passwords_cancel_the_verification(user):
    c = _verified_client(user)
    for _ in range(4):
        assert _change(c, current='nope').status_code == 400
    assert _change(c, current='nope').status_code == 403
    assert _change(c).status_code == 403


# ── success & the monthly limit ─────────────────────────────────────────────

def test_successful_change_updates_hash_and_timestamp_then_locks_for_a_month(user):
    c = _verified_client(user)
    r = _change(c)
    assert r.status_code == 200, r.get_json()
    row = _acc(user['username'])
    assert check_password_hash(row['passwordhash'], NEW_PW)
    assert NEW_PW not in row['passwordhash']
    assert row['password_changed_at'] is not None
    assert row['pw_otp_hash'] is None and row['pw_otp_verified_at'] is None

    nxt = r.get_json()['next_allowed_date']
    d = c.get('/api/user/password/eligibility').get_json()
    assert d['eligible'] is False and d['next_allowed_date'] == nxt
    assert d['message'] == ("You can only change your password once per month. "
                            f"You may change your password again on {nxt}.")
    r = c.post('/api/user/password/request-code')
    assert r.status_code == 403 and r.get_json()['error'] == 'not_eligible'
    # the same verification can't be replayed
    assert _change(c, current=NEW_PW, new='Another#2026').status_code == 403


def test_failed_attempts_do_not_start_the_monthly_limit(user):
    c = _verified_client(user)
    _change(c, current='wrong')
    assert _acc(user['username'])['password_changed_at'] is None
    assert c.get('/api/user/password/eligibility').get_json()['eligible'] is True


def test_limit_is_one_calendar_month(user):
    u = user['username']
    query_db("UPDATE accounts SET password_changed_at = '2026-01-31 10:00' WHERE username = %s RETURNING 1", [u])
    with app_module.app.app_context():
        conn = app_module.get_db_connection()
        try:
            acc = password_change._load_account(conn.cursor(cursor_factory=__import__('psycopg2.extras').extras.RealDictCursor), u)
        finally:
            conn.close()
    assert acc['next_allowed_at'] == datetime(2026, 2, 28, 10, 0)     # Jan 31 + 1 month
    query_db("UPDATE accounts SET password_changed_at = NOW() - INTERVAL '1 month' + INTERVAL '1 minute' "
             "WHERE username = %s RETURNING 1", [u])
    assert _client(u).get('/api/user/password/eligibility').get_json()['eligible'] is False
    query_db("UPDATE accounts SET password_changed_at = NOW() - INTERVAL '1 month' - INTERVAL '1 minute' "
             "WHERE username = %s RETURNING 1", [u])
    assert _client(u).get('/api/user/password/eligibility').get_json()['eligible'] is True


def test_cannot_target_another_account(user):
    before = _acc(user['other'])['passwordhash']
    c = _verified_client(user)
    r = _change(c, username=user['other'], userid='1')    # extra fields are ignored
    assert r.status_code == 200
    assert _acc(user['other'])['passwordhash'] == before
    assert check_password_hash(_acc(user['username'])['passwordhash'], NEW_PW)


def test_requires_login():
    c = app_module.app.test_client()
    for method, url in [('get', '/api/user/password/eligibility'), ('post', '/api/user/password/request-code'),
                        ('post', '/api/user/password/verify-code'), ('post', '/api/user/password/change')]:
        assert getattr(c, method)(url).status_code == 401


# ── Back button: /login no longer ends a live session ──────────────────────

def test_login_page_redirects_logged_in_user_to_their_dashboard(user):
    c = _client(user['username'])
    r = c.get('/login')
    assert r.status_code == 302 and r.headers['Location'].endswith('/faculty_dashboard')
    assert c.get('/api/user/me').status_code == 200          # still logged in


def test_login_page_is_not_cached_when_logged_out():
    r = app_module.app.test_client().get('/login')
    assert r.status_code == 200 and 'no-store' in r.headers['Cache-Control']
