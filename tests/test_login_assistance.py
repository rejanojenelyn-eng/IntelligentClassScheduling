"""
Contact Admin (login assistance requests): logged-out submission with validation, rate
limiting and duplicate protection, no account enumeration, Admin notification email,
and the Admin's Account Assistance workflow (status, remarks, identity verification
before any recovery action). Submitting never unlocks an account.

Uses a temporary account and test-only IPs (203.0.113.x); every row is removed afterwards.
"""
import secrets

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import app as app_module
import login_assistance as la
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
def ip():
    addr = f'203.0.113.{secrets.randbelow(250) + 1}'
    _q("DELETE FROM login_assistance_request WHERE request_ip = %s", (addr,))
    _q("DELETE FROM login_ip_failures WHERE ip = %s", (addr,))
    yield addr
    _q("DELETE FROM login_assistance_request WHERE request_ip = %s", (addr,))
    _q("DELETE FROM login_ip_failures WHERE ip = %s", (addr,))


@pytest.fixture
def account():
    username = 'assist_' + secrets.token_hex(4)
    _q("""INSERT INTO accounts (username, passwordhash, role, isactive, must_change_password,
                                account_setup_complete, email)
          VALUES (%s, %s, 'Admin', TRUE, FALSE, TRUE, %s)""",
       (username, generate_password_hash(PASSWORD), f'{username}@example.com'))
    yield username
    _q("DELETE FROM login_assistance_request WHERE LOWER(account_reference) = LOWER(%s)", (username,))
    _q("DELETE FROM login_throttle WHERE username_key = %s", (ls.username_key(username),))
    _q("DELETE FROM accounts WHERE username = %s", (username,))


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(la, 'SEND_IN_BACKGROUND', False)
    monkeypatch.setattr(la.mailer, 'configured', lambda cfg: True)
    monkeypatch.setattr(la.mailer, 'send_email', lambda cfg, to, subject, text, html=None: sent.append((to, subject, text, html)))
    return sent


def _submit(ref, ip, concern='Account Temporarily Locked', description=''):
    c = app_module.app.test_client()
    return c.post('/login/assistance', json={'account_reference': ref, 'concern': concern,
                                            'description': description}, environ_base={'REMOTE_ADDR': ip})


def _row(reference):
    rows = _q("""SELECT status, account_reference, userid, concern, description, identity_verified_at
                 FROM login_assistance_request WHERE reference = %s""", (reference,), fetch=True)
    return rows[0] if rows else None


@pytest.fixture
def admin():
    from database import query_db
    row = query_db("SELECT username FROM accounts WHERE role = 'Admin' AND isactive AND username NOT LIKE 'assist\\_%%' "
                   "ORDER BY userid LIMIT 1", one=True)
    if not row:
        pytest.skip('no active Admin account')
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, username=row['username'], role='Admin',
                 must_change_password=False, account_setup_complete=True)
    return client


def _flashes(client):
    with client.session_transaction() as s:
        return [m for _, m in s.get('_flashes', [])]


# ── Login page submission ──────────────────────────────────────────────────────

def test_request_is_saved_as_pending_with_a_reference(account, ip, mails):
    r = _submit(account, ip, description='Locked after typing my old password.')
    body = r.get_json()
    assert r.status_code == 200 and body['success']
    assert body['message'] == la.SUCCESS_MESSAGE and body['reference'].startswith('LA-')
    status, ref, userid, concern, desc, _ = _row(body['reference'])
    assert (status, ref, concern, desc) == ('Pending', account, 'Account Temporarily Locked',
                                            'Locked after typing my old password.')
    assert userid is not None                      # matched for the Admin only


def test_unknown_username_gets_the_same_answer(ip, mails):
    ghost = 'ghost_' + secrets.token_hex(4)
    body = _submit(ghost, ip).get_json()
    assert body['success'] and set(body) == {'success', 'reference', 'message'}
    assert _row(body['reference'])[2] is None       # no match, but the reply did not say so


def test_admin_is_notified_without_secrets(account, ip, mails):
    body = _submit(account, ip, concern='Forgot Password', description='my password is hunter2').get_json()
    assert len(mails) == 1
    to, subject, text, html = mails[0]
    assert to == app_module.Config.ADMIN_NOTIFY_EMAIL and body['reference'] in subject
    assert body['reference'] in text and 'Forgot Password' in text and 'Philippine Standard Time' in text
    assert 'hunter2' not in text and 'hunter2' not in html


def test_email_failure_still_saves_the_request(account, ip, monkeypatch):
    monkeypatch.setattr(la, 'SEND_IN_BACKGROUND', False)
    monkeypatch.setattr(la.mailer, 'configured', lambda cfg: True)
    def boom(*a, **k):
        raise RuntimeError('smtp down')
    monkeypatch.setattr(la.mailer, 'send_email', boom)
    body = _submit(account, ip).get_json()
    assert body['success'] and _row(body['reference'])[0] == 'Pending'


@pytest.mark.parametrize('payload, expect', [
    ({'account_reference': '', 'concern': 'Forgot Password'}, 'username or Employee ID'),
    ({'account_reference': 'bad name!', 'concern': 'Forgot Password'}, 'valid username'),
    ({'account_reference': 'someone', 'concern': 'Something else'}, 'concern'),
    ({'account_reference': 'someone', 'concern': 'Forgot Password', 'description': 'x' * 1001}, 'too long'),
])
def test_server_side_validation(ip, payload, expect):
    r = app_module.app.test_client().post('/login/assistance', json=payload, environ_base={'REMOTE_ADDR': ip})
    assert r.status_code == 400 and expect in r.get_json()['error']


def test_duplicate_open_request_is_refused(account, ip, mails):
    assert _submit(account, ip).get_json()['success']
    r = _submit(account.upper(), ip)
    assert r.status_code == 409 and r.get_json()['code'] == 'duplicate'
    assert len(_q("SELECT 1 FROM login_assistance_request WHERE LOWER(account_reference) = LOWER(%s)",
                  (account,), fetch=True)) == 1


def test_rate_limit_per_ip(ip, mails, monkeypatch):
    monkeypatch.setattr(la, 'IP_MAX_PER_HOUR', 2)
    names = ['rl_' + secrets.token_hex(4) for _ in range(3)]
    assert _submit(names[0], ip).status_code == 200
    assert _submit(names[1], ip).status_code == 200
    r = _submit(names[2], ip)
    assert r.status_code == 429 and r.get_json()['code'] == 'rate_limited'


def test_submitting_does_not_unlock_the_account(account, ip, mails, monkeypatch):
    monkeypatch.setattr(ls, 'SEND_IN_BACKGROUND', False)
    c = app_module.app.test_client()
    for _ in range(5):
        c.post('/login', data={'username': account, 'password': 'wrong'}, environ_base={'REMOTE_ADDR': ip})
    assert _submit(account, ip).get_json()['success']
    r = c.post('/login', data={'username': account, 'password': PASSWORD}, environ_base={'REMOTE_ADDR': ip})
    assert r.headers['Location'].endswith('/login')
    assert 'temporarily locked' in c.get('/login', environ_base={'REMOTE_ADDR': ip}).get_data(as_text=True)


# ── Admin: Account Assistance ──────────────────────────────────────────────────

def _request_id(reference):
    return _q("SELECT id FROM login_assistance_request WHERE reference = %s", (reference,), fetch=True)[0][0]


def test_admin_sees_the_request_and_the_button(account, ip, mails, admin):
    ref = _submit(account, ip).get_json()['reference']
    page = admin.get('/admin/account-assistance?status=All&q=' + account).get_data(as_text=True)
    assert ref in page and account in page and 'Account Assistance' in page
    um = admin.get('/admin/user-management').get_data(as_text=True)
    assert 'href="/admin/account-assistance"' in um and 'class="la-count"' in um
    assert app_module.app.test_client().get('/admin/account-assistance').status_code == 302   # not for guests


def test_status_flow_and_resolution_required(account, ip, mails, admin):
    rid = _request_id(_submit(account, ip).get_json()['reference'])
    url = f'/admin/account-assistance/{rid}/update'
    admin.post(url, data={'status': 'In Progress', 'admin_remarks': 'Calling the faculty member.'})
    assert _q("SELECT status, admin_remarks FROM login_assistance_request WHERE id = %s", (rid,), fetch=True)[0] \
        == ('In Progress', 'Calling the faculty member.')
    admin.post(url, data={'status': 'Resolved', 'resolution': ''})
    assert any('describe how the request was resolved' in m for m in _flashes(admin))
    admin.post(url, data={'status': 'Resolved', 'resolution': 'Lock expired; user signed in.'})
    status, resolution, resolved_at = _q("SELECT status, resolution, resolved_at FROM login_assistance_request "
                                         "WHERE id = %s", (rid,), fetch=True)[0]
    assert status == 'Resolved' and resolution == 'Lock expired; user signed in.' and resolved_at is not None


def test_recovery_actions_require_identity_verification(account, ip, mails, admin, monkeypatch):
    monkeypatch.setattr(ls, 'SEND_IN_BACKGROUND', False)
    c = app_module.app.test_client()
    for _ in range(5):
        c.post('/login', data={'username': account, 'password': 'wrong'}, environ_base={'REMOTE_ADDR': ip})
    rid = _request_id(_submit(account, ip).get_json()['reference'])

    admin.post(f'/admin/account-assistance/{rid}/unlock')
    assert any('Verify the person' in m for m in _flashes(admin))
    admin.post(f'/admin/account-assistance/{rid}/reset-password')
    assert check_password_hash(_q("SELECT passwordhash FROM accounts WHERE username = %s", (account,), fetch=True)[0][0],
                               PASSWORD)                                   # unchanged

    admin.post(f'/admin/account-assistance/{rid}/verify', data={'identity_method': 'Other (see notes)'})
    assert any('describe how the identity was verified' in m for m in _flashes(admin))
    admin.post(f'/admin/account-assistance/{rid}/verify',
               data={'identity_method': 'Registered email confirmed', 'identity_notes': 'Code confirmed by email.'})
    assert _q("SELECT status, identity_verified_at IS NOT NULL FROM login_assistance_request WHERE id = %s",
              (rid,), fetch=True)[0] == ('In Progress', True)

    admin.post(f'/admin/account-assistance/{rid}/unlock')
    lock = _q("SELECT locked_until FROM login_throttle WHERE username_key = %s", (ls.username_key(account),), fetch=True)
    assert lock[0][0] is None
    r = c.post('/login', data={'username': account, 'password': PASSWORD}, environ_base={'REMOTE_ADDR': ip})
    assert '/login' not in r.headers['Location']

    admin.post(f'/admin/account-assistance/{rid}/reset-password')
    pw, must = _q("SELECT passwordhash, must_change_password FROM accounts WHERE username = %s", (account,), fetch=True)[0]
    assert check_password_hash(pw, f'PUP@{account}') and must is True
    log = _q("SELECT actions_taken FROM login_assistance_request WHERE id = %s", (rid,), fetch=True)[0][0]
    assert 'Identity verified' in log and 'lock cleared' in log and 'temporary password' in log


def test_non_admin_cannot_manage_requests(account, ip, mails):
    rid = _request_id(_submit(account, ip).get_json()['reference'])
    faculty = app_module.app.test_client()
    with faculty.session_transaction() as s:
        s.update(loggedin=True, username=account, role='Faculty', account_setup_complete=True)
    faculty.post(f'/admin/account-assistance/{rid}/update', data={'status': 'Resolved', 'resolution': 'x'})
    assert _row(_q("SELECT reference FROM login_assistance_request WHERE id = %s", (rid,), fetch=True)[0][0])[0] == 'Pending'


def test_login_page_contact_admin_modal_markup():
    from database import query_db
    html = app_module.app.test_client().get('/login').get_data(as_text=True)
    assert 'id="caModal"' in html and 'Submit Request' in html and 'Cancel' in html
    for concern in la.CONCERNS:
        assert f'<option value="{concern}">' in html
