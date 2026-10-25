"""Account Setup saves nothing until Step 5's Save and Confirm.

Steps 1-5 keep everything in account_setup_draft (server-side); the account is written only
by Save and Confirm, in ONE transaction. Leave Setup (the Stay / Leave dialog on Step 1's
Back), logging out or logging in again discards the draft, so the account stays exactly as
it was.

Runs the real Flask routes against the local database inside one rolled-back transaction;
outgoing email is captured instead of sent. Photo files written by the tests are removed.
"""
import io
import os
import re

import pytest
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from tests.conftest import requires_db, DB_AVAILABLE
from tests._shared_tx import SharedTx, install

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module
    import contact_change
    import email_change

TEMP_PW, NEW_PW = 'TempPass123', 'NewPass456x'
ORIG_EMAIL, ORIG_CONTACT = 'leave.orig@example.com', '09175550301'
NEW_EMAIL, NEW_CONTACT, NEW_ID = 'leave.new@example.com', '09175550302', 'LEAVE-0001'


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (40, 40), (128, 0, 0)).save(buf, 'PNG')
    return buf.getvalue()


@pytest.fixture
def env(monkeypatch):
    t = SharedTx()
    install(monkeypatch, t)
    sent = []
    for mod in (email_change, contact_change):
        monkeypatch.setattr(mod, 'smtp_configured', lambda cfg: True)
    monkeypatch.setattr(email_change.mailer, 'send_email',
                        lambda cfg, to, subject, text, html=None: sent.append((to, text)))
    conn = t.handle()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT a.username, a.userid, a.role, f.employeenumber
        FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.isactive AND a.role IN ('Faculty', 'Academic Head')
          AND NOT EXISTS (SELECT 1 FROM local_arrangement_sessions ls
                          JOIN local_arrangement la ON la.arrangementid = ls.arrangementid
                          WHERE ls.faculty_employeenumber = f.employeenumber AND la.status = 'Archived')
        ORDER BY a.username LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        t.close()
        pytest.skip('no faculty account to test with')
    user = row['username']
    # An Admin-reset account: temporary password, must change it, setup not finished, an
    # existing photo / email / contact number.
    photo_name = f"profile_{secure_filename(user)}_leaveorig.png"
    photo_path = os.path.join(app_module._PROFILE_PHOTO_DIR, photo_name)
    with open(photo_path, 'wb') as fh:
        fh.write(_png())
    cur.execute("UPDATE accounts SET contactnumber = NULL WHERE BTRIM(contactnumber) IN (%s, %s)",
                (ORIG_CONTACT, NEW_CONTACT))
    cur.execute("UPDATE accounts SET email = NULL WHERE LOWER(email) IN (%s, %s)", (ORIG_EMAIL, NEW_EMAIL))
    cur.execute("""UPDATE accounts SET passwordhash = %s, must_change_password = TRUE, account_setup_complete = FALSE,
                          password_changed_at = NULL, email = %s, email_verified_addr = NULL,
                          contactnumber = %s, contact_verified = NULL, profile_photo = %s,
                          email_otp_sent_at = NULL, contact_otp_sent_at = NULL
                   WHERE userid = %s""",
                (generate_password_hash(TEMP_PW), ORIG_EMAIL, ORIG_CONTACT,
                 app_module._PROFILE_PHOTO_URL_PREFIX + photo_name, row['userid']))
    cur.execute("DELETE FROM account_setup_draft WHERE userid = %s", (row['userid'],))
    cur.execute("DELETE FROM personal_info_correction_request WHERE username = %s", (user,))
    cur.execute("""UPDATE faculty SET designationid = NULL,
                          employeetypeid = (SELECT employeetypeid FROM employeetype WHERE typename = 'Regular')
                   WHERE employeenumber = %s""", (row['employeenumber'],))
    conn.commit()
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, username=user, role=row['role'],
                 must_change_password=True, account_setup_complete=False)

    def q(sql, args=()):
        c = conn.cursor(cursor_factory=RealDictCursor); c.execute(sql, args)
        r = c.fetchall() if c.description else []
        c.close(); return r

    files = [photo_path]
    try:
        yield type('Env', (), dict(client=client, q=staticmethod(q), user=user, uid=row['userid'],
                                   role=row['role'], emp=row['employeenumber'], sent=sent,
                                   photo_url=app_module._PROFILE_PHOTO_URL_PREFIX + photo_name,
                                   photo_path=photo_path, files=files))
    finally:
        for f in files:
            if os.path.exists(f):
                os.remove(f)
        t.close()


def _account(env):
    return env.q("SELECT * FROM accounts WHERE userid = %s", (env.uid,))[0]


def _faculty(env, emp):
    rows = env.q("SELECT * FROM faculty WHERE employeenumber = %s", (emp,))
    return rows[0] if rows else None


def _draft(env):
    rows = env.q("SELECT * FROM account_setup_draft WHERE userid = %s", (env.uid,))
    return rows[0] if rows else None


def _code(env):
    return re.search(r'code is: (\d{6})', env.sent[-1][1]).group(1)


def _flashes(client):
    with client.session_transaction() as s:
        return [m for _, m in s.get('_flashes', [])]


def _assert_account_untouched(env, fac):
    acc = _account(env)
    assert check_password_hash(acc['passwordhash'], TEMP_PW) and acc['must_change_password'] is True
    assert acc['account_setup_complete'] is False
    assert (acc['email'], acc['contactnumber'], acc['profile_photo']) == (ORIG_EMAIL, ORIG_CONTACT, env.photo_url)
    assert acc['employeenumber'] == env.emp and _faculty(env, NEW_ID) is None
    now = _faculty(env, env.emp)
    for col in ('firstname', 'middlename', 'lastname', 'employeestatus', 'specializationid', 'employeetypeid'):
        assert now[col] == fac[col], col
    assert env.q("SELECT 1 FROM faculty_field_change_log WHERE employeenumber IN (%s, %s) "
                 "AND change_source = 'setup_edit'", (env.emp, NEW_ID)) == []


def _do_a_full_setup(env):
    """Enter every kind of information the way the setup steps do. Nothing reaches the account."""
    fac = _faculty(env, env.emp)
    assert env.client.get('/account-setup/password').status_code == 200
    r = env.client.post('/account-setup/password', data={
        'current_password': TEMP_PW, 'new_password': NEW_PW, 'confirm_password': NEW_PW})
    assert r.headers['Location'].endswith('/account-setup/photo')
    r = env.client.post('/account-setup/photo', data={'action': 'continue', 'photo': (io.BytesIO(_png()), 'me.png')},
                        content_type='multipart/form-data')
    assert r.headers['Location'].endswith('/account-setup/email')
    new_photo = _draft(env)['photo']
    env.files.append(os.path.join(app_module._PROFILE_PHOTO_DIR, os.path.basename(new_photo)))
    assert env.client.post('/api/user/email/request-code', data={'email': NEW_EMAIL}).status_code == 200
    r = env.client.post('/api/user/email/update', data={'email': NEW_EMAIL, 'code': _code(env)})
    assert r.status_code == 200 and r.get_json()['pending'] is True
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': NEW_CONTACT})
    assert r.headers['Location'].endswith('/account-setup/verify')        # no code in Step 4
    env.client.post('/account-setup/verify', data={
        'action': 'edit_name', 'First Name': 'Leavetest', 'Middle Name': '', 'Last Name': fac['lastname'],
        'Suffix': fac['suffix'] or ''})
    other_status = next(s for s in app_module._EMPLOYMENT_STATUSES if s != fac['employeestatus'])
    env.client.post('/account-setup/verify', data={'action': 'edit_employment', 'field': 'Employment Status',
                                                   'value': other_status})
    env.client.post('/account-setup/verify', data={'action': 'edit_employee_id', 'value': NEW_ID})
    return new_photo, fac, other_status


def test_nothing_is_saved_before_save_and_confirm(env):
    new_photo, fac, other_status = _do_a_full_setup(env)
    _assert_account_untouched(env, fac)
    # Everything is kept server-side; the new password only as a hash.
    d = _draft(env)
    assert check_password_hash(d['password_hash'], NEW_PW) and NEW_PW not in d['password_hash']
    assert (d['email'], d['contactnumber'], d['photo']) == (NEW_EMAIL, NEW_CONTACT, new_photo)
    assert d['faculty']['employeenumber'] == NEW_ID and d['faculty']['firstname'] == 'Leavetest'
    with env.client.session_transaction() as s:            # the browser cookie holds no password
        assert not any(isinstance(v, str) and ('pbkdf2' in v or 'scrypt' in v or NEW_PW in v) for v in s.values())
        assert s['setup_password_chosen'] is True
    # Step 5 shows the pending values for review.
    page = env.client.get('/account-setup/verify').data.decode()
    for shown in ('Leavetest', NEW_ID, NEW_EMAIL, NEW_CONTACT, other_status, new_photo):
        assert shown in page, shown
    # Back between steps keeps everything (and still saves nothing).
    env.client.post('/account-setup/contact', data={'action': 'back', 'contact_number': ''})
    env.client.post('/account-setup/email', data={'action': 'back', 'email': ''})
    assert NEW_EMAIL in env.client.get('/account-setup/email').data.decode()
    assert 'New password set' in env.client.get('/account-setup/password').data.decode()
    _assert_account_untouched(env, fac)
    # The old password still signs in until Save and Confirm.
    assert check_password_hash(_account(env)['passwordhash'], TEMP_PW)


def test_save_and_confirm_saves_everything_at_once(env):
    new_photo, fac, other_status = _do_a_full_setup(env)
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done')
    acc = _account(env)
    assert check_password_hash(acc['passwordhash'], NEW_PW) and acc['must_change_password'] is False
    assert acc['account_setup_complete'] is True
    assert (acc['email'], acc['email_verified_addr']) == (NEW_EMAIL, NEW_EMAIL)
    assert (acc['contactnumber'], acc['contact_verified']) == (NEW_CONTACT, NEW_CONTACT)
    assert acc['profile_photo'] == new_photo and not os.path.exists(env.photo_path)   # old photo file removed
    assert acc['employeenumber'] == NEW_ID and acc['userid'] == env.uid
    saved = _faculty(env, NEW_ID)
    assert saved['firstname'] == 'Leavetest' and saved['employeestatus'] == other_status
    assert _faculty(env, env.emp) is None
    logged = {r['field_name'] for r in env.q("SELECT field_name FROM faculty_field_change_log "
                                             "WHERE employeenumber = %s AND change_source = 'setup_edit'", (NEW_ID,))}
    assert {'Employee ID', 'First Name', 'Employment Status'} <= logged
    assert _draft(env) is None
    with env.client.session_transaction() as s:
        assert s['account_setup_complete'] is True and s['must_change_password'] is False
        assert 'setup_password_chosen' not in s


def test_a_failure_during_save_and_confirm_saves_nothing(env, monkeypatch):
    _, fac, _ = _do_a_full_setup(env)
    # (a) A value taken by someone else meanwhile: refused before anything is written.
    other = env.q("SELECT userid FROM accounts WHERE userid <> %s ORDER BY userid LIMIT 1", (env.uid,))[0]['userid']
    env.q("UPDATE accounts SET contactnumber = %s WHERE userid = %s RETURNING 1", (NEW_CONTACT, other))
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert any('contact number you entered is now used by another account' in m for m in _flashes(env.client))
    _assert_account_untouched(env, fac)
    assert _draft(env) is not None                                    # the user's inputs are kept
    env.q("UPDATE accounts SET contactnumber = NULL WHERE userid = %s RETURNING 1", (other,))
    # (b) A database error part-way (after the password was written): all of it is undone.
    real_rename = app_module._rename_employee_id
    def boom(cur, old, new):
        real_rename(cur, old, new)
        raise RuntimeError('simulated failure')
    monkeypatch.setattr(app_module, '_rename_employee_id', boom)
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert any('database error' in m for m in _flashes(env.client))
    _assert_account_untouched(env, fac)
    with env.client.session_transaction() as s:
        assert s['account_setup_complete'] is False


def test_leave_setup_discards_everything(env):
    new_photo, fac, _ = _do_a_full_setup(env)
    new_photo_path = os.path.join(app_module._PROFILE_PHOTO_DIR, os.path.basename(new_photo))
    r = env.client.post('/account-setup/leave')
    assert r.headers['Location'].endswith('/login')
    assert any('changes were discarded' in m for m in _flashes(env.client))
    with env.client.session_transaction() as s:
        assert 'loggedin' not in s                                             # signed out
    _assert_account_untouched(env, fac)
    assert _draft(env) is None and not os.path.exists(new_photo_path) and os.path.exists(env.photo_path)
    # The next login starts Account Setup again at Step 1.
    with env.client.session_transaction() as s:
        s.update(loggedin=True, username=env.user, role='Faculty',
                 must_change_password=True, account_setup_complete=False)
    assert env.client.get('/faculty_dashboard').headers['Location'].endswith('/account-setup/password')


def test_logging_out_or_in_again_discards_an_unfinished_setup(env):
    _, fac, _ = _do_a_full_setup(env)
    env.client.get('/logout')
    assert _draft(env) is None
    _assert_account_untouched(env, fac)
    # A draft left behind (e.g. the browser was closed) is dropped at the next login.
    env.q("""INSERT INTO account_setup_draft (userid, username, password_hash, email)
             VALUES (%s, %s, %s, %s) RETURNING 1""", (env.uid, env.user, generate_password_hash(NEW_PW), NEW_EMAIL))
    c = app_module.app.test_client()
    r = c.post('/login', data={'username': env.user, 'password': TEMP_PW})
    assert r.status_code == 302 and _draft(env) is None
    assert check_password_hash(_account(env)['passwordhash'], TEMP_PW)


def test_direct_profile_changes_wait_until_setup_is_finished(env):
    env.client.get('/account-setup/password')
    env.client.post('/account-setup/password', data={
        'current_password': TEMP_PW, 'new_password': NEW_PW, 'confirm_password': NEW_PW})
    for url, data in (('/api/user/profile/name', {'First Name': 'X', 'Last Name': 'Y'}),
                      ('/api/user/photo/upload', {}),
                      ('/api/user/password/request-code', {})):
        assert env.client.post(url, data=data).status_code == 403, url
    assert check_password_hash(_account(env)['passwordhash'], TEMP_PW)


def test_step1_back_asks_before_leaving(env):
    page = env.client.get('/account-setup/password').data.decode()
    assert 'Leaving Account Setup will discard all your changes. Do you want to continue?' in page
    assert 'Stay in Setup' in page and 'Leave Setup' in page
    assert 'id="btnBackToLogin"' in page and 'onclick="openLeaveDialog()"' in page
    assert 'href="/logout"' not in page                                    # Back no longer signs out directly
    assert 'action="/account-setup/leave"' in page
    # Only the confirmed POST leaves: a GET is sent back to Step 1 with nothing discarded.
    env.client.post('/account-setup/password', data={
        'current_password': TEMP_PW, 'new_password': NEW_PW, 'confirm_password': NEW_PW})
    r = env.client.get('/account-setup/leave')
    assert r.status_code in (302, 405) and '/login' not in r.headers.get('Location', '')
    assert _draft(env) is not None
    with env.client.session_transaction() as sess:
        assert sess.get('loggedin') is True
    # Coming back to Step 1 shows the kept password, with a way to choose a different one.
    page = env.client.get('/account-setup/password').data.decode()
    assert 'New password set' in page and 'change=1' in page
    assert 'id="pwNew"' in env.client.get('/account-setup/password?change=1').data.decode()
