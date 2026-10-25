"""
H2: the client IP behind the per-IP limits (login, forgot password / resend) cannot be
spoofed with forwarding headers. Only the source the deployment configured is trusted
(Config.CLIENT_IP_SOURCE, app._client_ip).

Uses throwaway usernames / identifiers, a temporary account (removed afterwards) and
documentation-only addresses (203.0.113.x, 198.51.100.x), so no real account or address
is ever limited. Rows these tests create are deleted at the end.
"""
import secrets

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

import app as app_module
import login_security as ls
import password_reset as pr
from conftest import requires_db

pytestmark = requires_db

SPOOFS = [  # what an attacker rotates on every request to look like a new client
    {'X-Forwarded-For': '1.1.1.{n}'},
    {'X-Real-IP': '2.2.2.{n}'},
    {'CF-Connecting-IP': '3.3.3.{n}', 'True-Client-IP': '4.4.4.{n}'},
    {'Forwarded': 'for=5.5.5.{n}'},
    {'X-Forwarded-For': '6.6.6.{n}, 7.7.7.{n}', 'X-Real-IP': '8.8.8.{n}'},
]


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


def _spoof(n):
    return {k: v.format(n=n) for k, v in SPOOFS[n % len(SPOOFS)].items()}


@pytest.fixture
def test_ip():
    addr = f'203.0.113.{secrets.randbelow(250) + 1}'
    yield addr
    for a in (addr, '198.51.100.7', '198.51.100.8'):
        _q("DELETE FROM login_ip_failures WHERE ip = %s", (a,))
        _q("DELETE FROM password_reset_requests WHERE request_ip = %s", (a,))


@pytest.fixture
def names():
    made = []
    def make(n):
        made.extend('ipspoof_' + secrets.token_hex(4) for _ in range(n))
        return made[-n:]
    yield make
    for u in made:
        _q("DELETE FROM login_throttle WHERE username_key = %s", (ls.username_key(u),))


@pytest.fixture
def admin_client():
    row = app_module.query_db("SELECT username FROM accounts WHERE role = 'Admin' AND isactive "
                              "ORDER BY username LIMIT 1", one=True)
    if not row:
        pytest.skip('no active Admin account')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=row['username'], role='Admin',
                 must_change_password=False, account_setup_complete=True)
    return c


@pytest.fixture
def source(monkeypatch):
    """Switch the configured client-IP source (and ProxyFix) for one test."""
    original = app_module.app.wsgi_app
    def use(src, hops=0):
        monkeypatch.setattr(app_module, '_CLIENT_IP_SOURCE', src)
        monkeypatch.setattr(app_module, '_TRUSTED_PROXY_HOPS', hops)
        if src == 'x-forwarded-for':
            monkeypatch.setattr(app_module.app, 'wsgi_app', ProxyFix(original, x_for=hops))
    return use


def _login(client, username, ip, headers):
    return client.post('/login', data={'username': username, 'password': 'wrong'},
                       headers=headers, environ_base={'REMOTE_ADDR': ip})


def _last_flash(client):
    with client.session_transaction() as s:
        return ' '.join(m for _, m in s.get('_flashes', []))


# ---- 1, 2, 4: spoofed headers cannot bypass the login limit (default: no proxy trusted) ---

def test_spoofed_headers_cannot_bypass_the_login_ip_limit(test_ip, names, monkeypatch, source):
    source('remote_addr')
    monkeypatch.setattr(ls, 'IP_MAX_FAILURES', 3)
    users = names(5)
    c = app_module.app.test_client()
    for n, u in enumerate(users[:3]):
        _login(c, u, test_ip, _spoof(n))                      # a "new" forged IP every time
    _login(c, users[3], test_ip, _spoof(3))
    assert ls.IP_LIMIT_MESSAGE in _last_flash(c)              # still counted as one client
    stored = {r[0] for r in _q("SELECT DISTINCT ip FROM login_ip_failures WHERE failed_at > NOW() - INTERVAL '1 minute' "
                               "AND ip IN (%s, '1.1.1.0', '2.2.2.1', '3.3.3.2')", (test_ip,), fetch=True)}
    assert stored == {test_ip}                                 # no forged address was ever recorded


def test_spoofed_headers_cannot_bypass_the_forgot_password_limit(test_ip, monkeypatch, source):
    source('remote_addr')
    monkeypatch.setattr(pr, 'IP_MAX_REQUESTS', 2)
    c = app_module.app.test_client()
    for n in range(2):
        r = c.post('/forgot-password', data={'identifier': 'nobody_' + secrets.token_hex(4)},
                   headers=_spoof(n), environ_base={'REMOTE_ADDR': test_ip})
        assert r.status_code == 302                            # accepted (generic answer, no email)
    r = c.post('/forgot-password', data={'identifier': 'nobody_' + secrets.token_hex(4)},
               headers=_spoof(7), environ_base={'REMOTE_ADDR': test_ip})
    assert r.status_code == 429 and b'Too many password reset requests' in r.data


def test_different_real_clients_are_still_counted_separately(test_ip, names, monkeypatch, source):
    source('remote_addr')
    monkeypatch.setattr(ls, 'IP_MAX_FAILURES', 2)
    users = names(3)
    c = app_module.app.test_client()
    _login(c, users[0], test_ip, {}); _login(c, users[1], test_ip, {})
    _login(c, users[2], '198.51.100.7', {})                    # another network: not limited
    assert ls.IP_LIMIT_MESSAGE not in _last_flash(c)


# ---- 3: trusted proxy forwarding is read correctly, the client's part never ---------------

@pytest.mark.parametrize('hops,xff,expected', [
    (1, '9.9.9.9', '9.9.9.9'),                                  # one proxy appended the real IP
    (1, '1.1.1.1, 9.9.9.9', '9.9.9.9'),                         # client forged the leftmost entry
    (1, '1.1.1.1, 2.2.2.2, 9.9.9.9', '9.9.9.9'),
    (2, '1.1.1.1, 9.9.9.9, 10.0.0.5', '9.9.9.9'),               # two trusted proxies
])
def test_x_forwarded_for_uses_only_the_trusted_hops(source, admin_client, hops, xff, expected):
    source('x-forwarded-for', hops)
    # A real request through ProxyFix -> _client_ip (read back via the Admin diagnostics page).
    d = admin_client.get('/admin/diagnostics/client-ip', headers={'X-Forwarded-For': xff},
                         environ_base={'REMOTE_ADDR': '10.0.0.1'}).get_json()
    assert d['client_ip_used_for_limits'] == expected
    assert d['tcp_peer'] == '10.0.0.1'


def test_x_forwarded_for_with_too_few_entries_falls_back_to_the_peer(source):
    source('x-forwarded-for', 2)
    with app_module.app.test_request_context(environ_base={'REMOTE_ADDR': '10.0.0.1'}):
        assert app_module._client_ip() == '10.0.0.1'

    # ProxyFix: fewer entries than trusted hops -> the header is not trusted at all
    seen = {}
    def wsgi(environ, start_response):
        seen['ip'] = environ['REMOTE_ADDR']; start_response('200 OK', []); return [b'']
    ProxyFix(wsgi, x_for=2)({'REMOTE_ADDR': '10.0.0.1', 'HTTP_X_FORWARDED_FOR': '1.1.1.1',
                             'REQUEST_METHOD': 'GET', 'wsgi.url_scheme': 'http'}, lambda *a: None)
    assert seen['ip'] == '10.0.0.1'


def test_x_real_ip_mode_trusts_only_a_valid_proxy_set_value(source):
    source('x-real-ip')
    with app_module.app.test_request_context(headers={'X-Real-IP': '9.9.9.9', 'X-Forwarded-For': '1.1.1.1'},
                                             environ_base={'REMOTE_ADDR': '10.0.0.1'}):
        assert app_module._client_ip() == '9.9.9.9'
    for bad in ('not-an-ip', '9.9.9.9, 1.1.1.1', '', '<script>'):
        with app_module.app.test_request_context(headers={'X-Real-IP': bad}, environ_base={'REMOTE_ADDR': '10.0.0.1'}):
            assert app_module._client_ip() == '10.0.0.1', bad


def test_x_real_ip_mode_limits_the_real_client_despite_a_forged_x_forwarded_for(test_ip, names, monkeypatch, source):
    source('x-real-ip')
    monkeypatch.setattr(ls, 'IP_MAX_FAILURES', 2)
    users = names(3)
    c = app_module.app.test_client()
    for n, u in enumerate(users):                              # the proxy always sets the same X-Real-IP
        _login(c, u, '10.0.0.1', {'X-Real-IP': '198.51.100.8', 'X-Forwarded-For': f'1.1.1.{n}'})
    assert ls.IP_LIMIT_MESSAGE in _last_flash(c)


# ---- configuration --------------------------------------------------------------------------

@pytest.mark.parametrize('src,hops,expected', [
    ('remote_addr', 0, ('remote_addr', 0)),
    ('x-real-ip', 3, ('x-real-ip', 0)),
    ('x-forwarded-for', 1, ('x-forwarded-for', 1)),
    ('x-forwarded-for', 0, ('remote_addr', 0)),                 # no hop count: trust nothing
    ('x-forwarded-for', 9, ('remote_addr', 0)),
    ('cf-connecting-ip', 0, ('remote_addr', 0)),                # unknown source: trust nothing
])
def test_invalid_settings_fall_back_to_the_safe_default(src, hops, expected):
    assert app_module._client_ip_config(src, hops) == expected


def test_default_is_the_tcp_peer_for_local_development():
    assert app_module.Config.CLIENT_IP_SOURCE in ('remote_addr', '') or app_module._CLIENT_IP_SOURCE == 'remote_addr'
    with app_module.app.test_request_context(headers={'X-Forwarded-For': '1.1.1.1', 'X-Real-IP': '2.2.2.2'},
                                             environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        assert app_module._client_ip() == '127.0.0.1'
    r = app_module.app.test_client().get('/login', headers={'Sec-Fetch-Mode': 'navigate'})
    assert r.status_code == 200


# ---- 5: account lockout still works, and forged headers don't reset it ----------------------

def test_account_lockout_still_works_with_rotating_forged_headers(test_ip, source, monkeypatch):
    source('remote_addr')
    monkeypatch.setattr(ls, 'SEND_IN_BACKGROUND', False)
    monkeypatch.setattr(ls.mailer, 'configured', lambda cfg: False)
    user = 'lockspoof_' + secrets.token_hex(4)
    _q("""INSERT INTO accounts (username, passwordhash, role, isactive, must_change_password, account_setup_complete,
                                email)
          VALUES (%s, %s, 'Admin', TRUE, FALSE, TRUE, %s)""",
       (user, generate_password_hash('Right#Pass123'), f'{user}@example.com'))
    try:
        c = app_module.app.test_client()
        msgs = []
        for n in range(5):
            _login(c, user, test_ip, _spoof(n))
            msgs.append(_last_flash(c))
        assert 'Invalid username or password.' in msgs[0]
        assert 'Warning: 2 attempts remaining' in msgs[2]
        assert 'Warning: 1 attempt remaining' in msgs[3]
        assert ls.LOCKED_MESSAGE.split('.')[0] in msgs[4]
        page = c.get('/login', environ_base={'REMOTE_ADDR': test_ip}).get_data(as_text=True)
        assert 'href="/forgot-password?restart=1"' in page                          # suggested after lockout
        r = c.post('/login', data={'username': user, 'password': 'Right#Pass123'},
                   headers={'X-Forwarded-For': '9.9.9.9'}, environ_base={'REMOTE_ADDR': '198.51.100.7'})
        assert r.headers['Location'].endswith('/login')                             # still locked elsewhere
    finally:
        _q("DELETE FROM login_throttle WHERE username_key = %s", (ls.username_key(user),))
        _q("DELETE FROM accounts WHERE username = %s", (user,))


# ---- diagnostics endpoint is Admin-only ------------------------------------------------------

def test_client_ip_diagnostics_is_admin_only():
    anon = app_module.app.test_client()
    assert anon.get('/admin/diagnostics/client-ip', headers={'Sec-Fetch-Mode': 'cors'}).status_code == 401
    rows = app_module.query_db("SELECT username, role FROM accounts WHERE isactive AND role IN ('Admin','Faculty') "
                               "ORDER BY role", ())
    by_role = {r['role']: r['username'] for r in rows}
    if 'Faculty' in by_role:
        fc = app_module.app.test_client()
        with fc.session_transaction() as s:
            s.update(loggedin=True, username=by_role['Faculty'], role='Faculty',
                     must_change_password=False, account_setup_complete=True)
        assert fc.get('/admin/diagnostics/client-ip', headers={'Sec-Fetch-Mode': 'cors'}).status_code == 403
    if 'Admin' not in by_role:
        pytest.skip('no active Admin account')
    ac = app_module.app.test_client()
    with ac.session_transaction() as s:
        s.update(loggedin=True, username=by_role['Admin'], role='Admin',
                 must_change_password=False, account_setup_complete=True)
    d = ac.get('/admin/diagnostics/client-ip', headers={'X-Forwarded-For': '1.1.1.1, 9.9.9.9', 'X-Real-IP': '2.2.2.2'},
               environ_base={'REMOTE_ADDR': '10.0.0.1'}).get_json()
    assert d['configured_source'] == 'remote_addr' and d['client_ip_used_for_limits'] == '10.0.0.1'
    assert d['x_forwarded_for_from_right'] == ['9.9.9.9', '1.1.1.1'] and d['x_real_ip'] == ['2.2.2.2']
