"""
Login protection: 5 consecutive wrong passwords -> 5-minute lock (any role), warnings
on the 3rd and 4th, one security email per lockout, Forgot Password / Contact Admin on
the lock notice, per-IP rate limit, no account enumeration, no bypass by racing.

Uses a temporary account (removed afterwards) and test-only IPs (203.0.113.x), so no
real account is ever locked.
"""
import secrets
import threading

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import login_security as ls
from conftest import requires_db

pytestmark = requires_db

PASSWORD = 'Correct#Pass123'


def _q(sql, args=(), fetch=False):
    from database import get_db_connection
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute(sql, args)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    finally:
        cur.close(); conn.close()


@pytest.fixture
def account():
    username = 'locktest_' + secrets.token_hex(4)
    _q("""INSERT INTO accounts (username, passwordhash, role, isactive, must_change_password,
                                account_setup_complete, email)
          VALUES (%s, %s, 'Admin', TRUE, FALSE, TRUE, %s)""",
       (username, generate_password_hash(PASSWORD), f'{username}@example.com'))
    yield username
    _q("DELETE FROM login_throttle WHERE username_key = %s", (ls.username_key(username),))
    _q("DELETE FROM accounts WHERE username = %s", (username,))


@pytest.fixture
def ip():
    addr = f'203.0.113.{secrets.randbelow(250) + 1}'
    _q("DELETE FROM login_ip_failures WHERE ip = %s", (addr,))
    yield addr
    _q("DELETE FROM login_ip_failures WHERE ip = %s", (addr,))


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(ls, 'SEND_IN_BACKGROUND', False)
    monkeypatch.setattr(ls.mailer, 'configured', lambda cfg: True)
    monkeypatch.setattr(ls.mailer, 'send_email', lambda cfg, to, subject, text, html=None: sent.append((to, subject, text, html)))
    return sent


def _login(client, username, password, ip):
    r = client.post('/login', data={'username': username, 'password': password},
                    environ_base={'REMOTE_ADDR': ip})
    return r, client.get('/login', environ_base={'REMOTE_ADDR': ip}).get_data(as_text=True)


def _throttle(username):
    rows = _q("SELECT failed_count, locked_until FROM login_throttle WHERE username_key = %s",
              (ls.username_key(username),), fetch=True)
    return rows[0] if rows else None


def test_five_wrong_passwords_warn_then_lock(account, ip, mails):
    c = app_module.app.test_client()
    for n in (1, 2):
        _, page = _login(c, account, 'wrong', ip)
        assert 'Invalid username or password.' in page and 'Warning:' not in page and 'class="lock-box"' not in page
    _, page = _login(c, account, 'wrong', ip)
    assert 'Warning: 2 attempts remaining' in page
    _, page = _login(c, account, 'wrong', ip)
    assert 'Warning: 1 attempt remaining' in page
    _, page = _login(c, account, 'wrong', ip)
    assert ls.LOCKED_MESSAGE in page and 'class="lock-box"' in page
    assert 'href="/forgot-password?restart=1"' in page and 'Forgot Password' in page
    assert "openContactAdmin('Account Temporarily Locked')" in page and 'Contact Admin' in page
    assert 'id="caModal"' in page
    assert _throttle(account)[0] == 5 and _throttle(account)[1] is not None


def test_locked_account_rejects_even_the_correct_password(account, ip, mails):
    c = app_module.app.test_client()
    for _ in range(5):
        _login(c, account, 'wrong', ip)
    r, page = _login(app_module.app.test_client(), account, PASSWORD, ip)    # another browser/device
    assert r.headers['Location'].endswith('/login')
    assert 'temporarily locked' in page and 'try again in about 5 minutes' in page
    with c.session_transaction() as s:
        assert not s.get('loggedin')


def test_login_works_again_after_the_lock_expires_and_resets_the_counter(account, ip, mails):
    c = app_module.app.test_client()
    for _ in range(5):
        _login(c, account, 'wrong', ip)
    _q("UPDATE login_throttle SET locked_until = NOW() - INTERVAL '1 second' WHERE username_key = %s",
       (ls.username_key(account),))
    r = c.post('/login', data={'username': account, 'password': PASSWORD}, environ_base={'REMOTE_ADDR': ip})
    assert '/login' not in r.headers['Location']
    assert _throttle(account)[0] == 0 and _throttle(account)[1] is None


def test_successful_login_resets_the_counter(account, ip, mails):
    c = app_module.app.test_client()
    _login(c, account, 'wrong', ip); _login(c, account, 'wrong', ip)
    r = app_module.app.test_client().post('/login', data={'username': account, 'password': PASSWORD},
                                          environ_base={'REMOTE_ADDR': ip})
    assert '/login' not in r.headers['Location']
    assert _throttle(account)[0] == 0
    _, page = _login(app_module.app.test_client(), account, 'wrong', ip)
    assert 'Invalid username or password.' in page and 'Warning' not in page


def test_one_security_email_per_lockout(account, ip, mails):
    c = app_module.app.test_client()
    for _ in range(5):
        _login(c, account, 'wrong', ip)
    assert len(mails) == 1
    to, subject, text, html = mails[0]
    assert to == f'{account}@example.com' and 'temporarily locked' in subject
    assert 'Philippine Standard Time' in text and '5 minutes' in text and 'change your password' in text
    assert 'wrong' not in text and PASSWORD not in text and 'wrong' not in html
    for _ in range(3):                                   # attempts during the lock: no more mail
        _login(c, account, PASSWORD, ip)
    assert len(mails) == 1


def test_email_failure_does_not_stop_the_lockout(account, ip, monkeypatch):
    monkeypatch.setattr(ls, 'SEND_IN_BACKGROUND', False)
    monkeypatch.setattr(ls.mailer, 'configured', lambda cfg: True)
    def boom(*a, **k):
        raise RuntimeError('smtp down')
    monkeypatch.setattr(ls.mailer, 'send_email', boom)
    c = app_module.app.test_client()
    for _ in range(5):
        _, page = _login(c, account, 'wrong', ip)
    assert ls.LOCKED_MESSAGE in page and _throttle(account)[1] is not None


def test_unknown_username_gets_identical_responses_and_no_email(ip, mails):
    ghost = 'nobody_' + secrets.token_hex(6)
    c = app_module.app.test_client()
    try:
        pages = [_login(c, ghost, 'wrong', ip)[1] for _ in range(5)]
        assert 'Invalid username or password.' in pages[0]
        assert 'Warning: 2 attempts remaining' in pages[2] and 'Warning: 1 attempt remaining' in pages[3]
        assert ls.LOCKED_MESSAGE in pages[4]
        assert mails == []
    finally:
        _q("DELETE FROM login_throttle WHERE username_key = %s", (ls.username_key(ghost),))


def test_username_case_does_not_reset_the_counter(account, ip, mails):
    c = app_module.app.test_client()
    _login(c, account, 'wrong', ip); _login(c, account.upper(), 'wrong', ip)
    _, page = _login(c, account.title(), 'wrong', ip)
    assert 'Warning: 2 attempts remaining' in page


def test_per_ip_rate_limit(ip, monkeypatch):
    monkeypatch.setattr(ls, 'IP_MAX_FAILURES', 3)
    names = ['iplimit_' + secrets.token_hex(4) for _ in range(4)]
    c = app_module.app.test_client()
    try:
        for n in names[:3]:
            _login(c, n, 'wrong', ip)
        _, page = _login(c, names[3], 'wrong', ip)
        assert 'Too many login attempts from this network' in page
    finally:
        for n in names:
            _q("DELETE FROM login_throttle WHERE username_key = %s", (ls.username_key(n),))


def test_simultaneous_attempts_cannot_exceed_the_limit(account, ip, monkeypatch):
    from database import get_db_connection
    monkeypatch.setattr(ls, 'SEND_IN_BACKGROUND', False)
    results, lock = [], threading.Lock()

    def worker():
        conn = get_db_connection()
        try:
            res = ls.attempt(conn, account, 'wrong', ip)
        finally:
            conn.close()
        with lock:
            results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    kinds = sorted(r['result'] for r in results)
    assert kinds.count('invalid') == 4 and kinds.count('locked') == 6
    assert sum(1 for r in results if r.get('new_lock')) == 1           # exactly one lockout event
    assert _throttle(account)[0] == 5
