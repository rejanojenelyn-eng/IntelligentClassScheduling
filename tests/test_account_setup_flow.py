"""
First-login account setup (Faculty):

  Change Password (required, no Skip) → Email (Skip) → Profile Picture (Skip)
  → Contact Number (Skip) → Review & Complete

Every step has Back; Back keeps what was typed and can never get past the
password change. Profile pictures are stored as files in
static/uploads/profile_photos/ with only the path kept in accounts.profile_photo.

Uses one real Faculty account; its accounts row, faculty email/contact and any
photo files created are restored/removed afterwards.
"""
import io
import os

import pytest
from werkzeug.security import generate_password_hash, check_password_hash

from conftest import requires_db, DB_AVAILABLE

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module
    from database import query_db

OLD_PW, NEW_PW = 'Default#123', 'MyNewPass#1'
_ACC_COLS = ('passwordhash', 'must_change_password', 'account_setup_complete', 'profile_photo',
             'password_changed_at')


def _png(size=(40, 40), color=(128, 0, 0), fmt='PNG'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def fac(monkeypatch):
    row = query_db("""
        SELECT a.username, a.employeenumber FROM accounts a
        JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.role = 'Faculty' AND a.isactive AND a.username NOT IN ('12079')
        ORDER BY a.username LIMIT 1 OFFSET 7""", one=True)
    u, emp = row['username'], row['employeenumber']
    acc_snap = dict(query_db(f"SELECT {', '.join(_ACC_COLS)} FROM accounts WHERE username = %s", [u], one=True))
    fac_snap = dict(query_db("SELECT email, contactnumber FROM faculty WHERE employeenumber = %s", [emp], one=True))
    photo_dir = app_module._PROFILE_PHOTO_DIR
    files_before = set(os.listdir(photo_dir))
    query_db("""UPDATE accounts SET passwordhash = %s, must_change_password = TRUE,
                account_setup_complete = FALSE, profile_photo = NULL WHERE username = %s RETURNING 1""",
             [generate_password_hash(OLD_PW), u])
    query_db("UPDATE faculty SET email = %s, contactnumber = %s WHERE employeenumber = %s RETURNING 1",
             [f'setup.test.{emp}@pup-lopez.test', '09170000000', emp])
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=u, role='Faculty',
                 must_change_password=True, account_setup_complete=False)
    try:
        yield {'c': c, 'u': u, 'emp': emp}
    finally:
        query_db(f"UPDATE accounts SET {', '.join(k + ' = %s' for k in acc_snap)} WHERE username = %s RETURNING 1",
                 list(acc_snap.values()) + [u])
        query_db("UPDATE faculty SET email = %s, contactnumber = %s WHERE employeenumber = %s RETURNING 1",
                 [fac_snap['email'], fac_snap['contactnumber'], emp])
        for f in set(os.listdir(photo_dir)) - files_before:
            os.remove(os.path.join(photo_dir, f))


def _loc(r):
    return r.headers.get('Location', '')


def _change_password(f):
    return f['c'].post('/account-setup/password', data={
        'current_password': OLD_PW, 'new_password': NEW_PW, 'confirm_password': NEW_PW})


def _acc(u):
    return query_db("SELECT * FROM accounts WHERE username = %s", [u], one=True)


def _fac(emp):
    return query_db("SELECT email, contactnumber FROM faculty WHERE employeenumber = %s", [emp], one=True)


# ── Step 1: password is required and can't be skipped or bypassed ──────────

def test_password_step_has_back_to_login_and_no_skip(fac):
    html = fac['c'].get('/account-setup/password').get_data(as_text=True)
    assert 'BACK TO LOGIN' in html and 'value="skip"' not in html and '>SKIP<' not in html
    assert '(required)' in html


@pytest.mark.parametrize('path', ['/account-setup/email', '/account-setup/photo',
                                  '/account-setup/contact', '/account-setup/verify', '/faculty_dashboard'])
def test_nothing_is_reachable_before_the_password_change(fac, path):
    r = fac['c'].get(path)
    assert r.status_code == 302 and _loc(r).endswith('/account-setup/password')


def test_skip_or_empty_post_does_not_change_the_password(fac):
    for data in ({'action': 'skip'}, {'action': 'back'}, {}):
        r = fac['c'].post('/account-setup/password', data=data)
        assert _loc(r).endswith('/account-setup/password')
    assert _acc(fac['u'])['must_change_password'] is True


@pytest.mark.parametrize('new,msg', [
    ('abc12', 'at least 8 characters'),
    ('abcdefgh', 'letters and numbers'),
    ('12345678', 'letters and numbers'),
    ('password123', 'too common'),
])
def test_weak_new_password_is_refused_with_a_designed_message(fac, new, msg):
    c = fac['c']
    r = c.post('/account-setup/password', data={
        'current_password': OLD_PW, 'new_password': new, 'confirm_password': new}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert 'class="alert-error"' in html and msg in html
    assert _acc(fac['u'])['must_change_password'] is True


def test_new_password_may_not_contain_the_employee_number(fac):
    weak = f"Pw{fac['u']}x9"
    r = fac['c'].post('/account-setup/password', data={
        'current_password': OLD_PW, 'new_password': weak, 'confirm_password': weak}, follow_redirects=True)
    assert 'must not contain your employee number' in r.get_data(as_text=True)


def test_password_step_shows_the_security_notice_and_checklist(fac):
    html = fac['c'].get('/account-setup/password').get_data(as_text=True)
    assert 'Secure your account' in html and 'temporary password' in html
    assert 'id="pwRules"' in html and 'At least 8 characters' in html


def test_password_change_moves_to_email(fac):
    r = _change_password(fac)
    assert _loc(r).endswith('/account-setup/email')
    acc = _acc(fac['u'])
    assert check_password_hash(acc['passwordhash'], NEW_PW) and acc['must_change_password'] is False


def test_back_to_step_one_after_changing_shows_it_as_done(fac):
    _change_password(fac)
    html = fac['c'].get('/account-setup/password').get_data(as_text=True)
    assert 'Password changed' in html and 'name="current_password"' not in html
    assert _loc(fac['c'].post('/account-setup/password')).endswith('/account-setup/email')


# ── Step 2: email (optional) ───────────────────────────────────────────────

def test_email_skip_back_and_save(fac):
    c = fac['c']; _change_password(fac)
    before = _fac(fac['emp'])['email']
    html = c.get('/account-setup/email').get_data(as_text=True)
    assert 'value="back"' in html and 'value="skip"' in html and 'value="continue"' in html

    # Back keeps the typed value
    r = c.post('/account-setup/email', data={'action': 'back', 'email': 'typed@pup.test'})
    assert _loc(r).endswith('/account-setup/password')
    assert 'value="typed@pup.test"' in c.get('/account-setup/email').get_data(as_text=True)
    assert _fac(fac['emp'])['email'] == before                   # nothing saved by Back

    # Skip leaves the email on file as it is
    assert _loc(c.post('/account-setup/email', data={'action': 'skip', 'email': 'x@y.z'})).endswith('/account-setup/photo')
    assert _fac(fac['emp'])['email'] == before

    # Invalid → stays, keeps what was typed
    r = c.post('/account-setup/email', data={'action': 'continue', 'email': 'not-an-email'})
    assert _loc(r).endswith('/account-setup/email')
    assert 'value="not-an-email"' in c.get('/account-setup/email').get_data(as_text=True)

    # Valid → saved to faculty.email
    new = f"new.{fac['emp']}@pup-lopez.test"
    assert _loc(c.post('/account-setup/email', data={'action': 'continue', 'email': new})).endswith('/account-setup/photo')
    assert _fac(fac['emp'])['email'] == new


def test_email_already_used_by_someone_else(fac):
    c = fac['c']; _change_password(fac)
    other = query_db("SELECT email FROM faculty WHERE email IS NOT NULL AND employeenumber <> %s LIMIT 1",
                     [fac['emp']], one=True)['email']
    r = c.post('/account-setup/email', data={'action': 'continue', 'email': other}, follow_redirects=True)
    assert 'already used by another faculty member' in r.get_data(as_text=True)


# ── Step 3: profile picture (optional) ─────────────────────────────────────

def _upload(c, data, name='me.png', action='continue'):
    return c.post('/account-setup/photo', data={'action': action, 'photo': (io.BytesIO(data), name)},
                  content_type='multipart/form-data')


def test_photo_is_stored_as_a_file_and_linked_to_the_account(fac):
    c = fac['c']; _change_password(fac)
    r = _upload(c, _png())
    assert _loc(r).endswith('/account-setup/contact')
    path = _acc(fac['u'])['profile_photo']
    assert path.startswith('/static/uploads/profile_photos/profile_')
    fname = os.path.basename(path)
    assert os.path.isfile(os.path.join(app_module._PROFILE_PHOTO_DIR, fname))
    # shown as the user's picture
    me = c.get('/api/user/me').get_json()
    assert me['photo_url'] == path

    # a new upload gets a new name and the old file is removed
    _upload(c, _png(color=(0, 90, 0)), name='again.jpg')
    path2 = _acc(fac['u'])['profile_photo']
    assert path2 != path
    assert not os.path.exists(os.path.join(app_module._PROFILE_PHOTO_DIR, fname))


def test_photo_skip_and_required_choice_on_continue(fac):
    c = fac['c']; _change_password(fac)
    r = c.post('/account-setup/photo', data={'action': 'continue'})
    assert _loc(r).endswith('/account-setup/photo')            # nothing chosen → choose or Skip
    assert _loc(c.post('/account-setup/photo', data={'action': 'skip'})).endswith('/account-setup/contact')
    assert _acc(fac['u'])['profile_photo'] is None


def test_photo_chosen_then_back_is_kept(fac):
    c = fac['c']; _change_password(fac)
    r = _upload(c, _png(), action='back')
    assert _loc(r).endswith('/account-setup/email')
    assert _acc(fac['u'])['profile_photo']
    assert 'CHANGE' in c.get('/account-setup/photo').get_data(as_text=True)


@pytest.mark.parametrize('data,name,msg', [
    (b'this is not an image', 'fake.png', 'not a valid JPG or PNG'),
    (b'GIF89a' + b'\x00' * 50, 'anim.gif', 'not a valid JPG or PNG'),
])
def test_bad_files_are_rejected(fac, data, name, msg):
    c = fac['c']; _change_password(fac)
    r = _upload(c, data, name=name)
    assert _loc(r).endswith('/account-setup/photo')
    assert msg in c.get('/account-setup/photo').get_data(as_text=True)
    assert _acc(fac['u'])['profile_photo'] is None


def test_photo_over_2mb_is_rejected(fac):
    c = fac['c']; _change_password(fac)
    big = _png(size=(1600, 1600))
    big += b'\x00' * max(0, 2 * 1024 * 1024 + 10 - len(big))
    _upload(c, big)
    assert 'maximum size is 2 MB' in c.get('/account-setup/photo').get_data(as_text=True)


# ── Step 4: contact number (optional) ──────────────────────────────────────

def test_contact_skip_back_and_save(fac):
    c = fac['c']; _change_password(fac)
    before = _fac(fac['emp'])['contactnumber']
    r = c.post('/account-setup/contact', data={'action': 'back', 'contact_number': '0917 555 1234'})
    assert _loc(r).endswith('/account-setup/photo')
    assert 'value="0917 555 1234"' in c.get('/account-setup/contact').get_data(as_text=True)
    assert _loc(c.post('/account-setup/contact', data={'action': 'skip'})).endswith('/account-setup/verify')
    assert _fac(fac['emp'])['contactnumber'] == before
    assert _loc(c.post('/account-setup/contact', data={'action': 'continue', 'contact_number': 'abc'})
                ).endswith('/account-setup/contact')
    assert _loc(c.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '09181234567'})
                ).endswith('/account-setup/verify')
    assert _fac(fac['emp'])['contactnumber'] == '09181234567'


# ── Step 5: review & complete ──────────────────────────────────────────────

def test_skip_everything_then_complete(fac):
    c = fac['c']; _change_password(fac)
    for step in ('email', 'photo', 'contact'):
        c.post(f'/account-setup/{step}', data={'action': 'skip'})
    html = c.get('/account-setup/verify').get_data(as_text=True)
    assert 'href="/account-setup/contact"' in html and 'BACK' in html
    r = c.post('/account-setup/verify', data={'action': 'confirm'})
    assert _loc(r).endswith('/faculty_dashboard')
    assert _acc(fac['u'])['account_setup_complete'] is True
    with c.session_transaction() as s:
        assert 'setup_draft' not in s
    # finished: the setup pages send you to the dashboard now
    assert _loc(c.get('/account-setup/email')).endswith('/faculty_dashboard')


def test_old_info_url_goes_to_the_email_step(fac):
    _change_password(fac)
    assert _loc(fac['c'].get('/account-setup/info')).endswith('/account-setup/email')


# ── Profile Settings: own profile picture can be changed ───────────────────

def test_profile_settings_photo_upload(fac):
    c = fac['c']
    with c.session_transaction() as s:
        s.update(must_change_password=False, account_setup_complete=True)
    me = c.get('/api/user/me').get_json()
    assert me['photo_editable'] is True and me['profile_editable'] is False
    r = c.post('/api/user/photo/upload', data={'photo': (io.BytesIO(_png(fmt='JPEG')), 'x.jpg')},
               content_type='multipart/form-data')
    assert r.status_code == 200 and r.get_json()['photo_url'].endswith('.jpg')
    assert c.get('/api/user/me').get_json()['photo_url'] == r.get_json()['photo_url']
    bad = c.post('/api/user/photo/upload', data={'photo': (io.BytesIO(b'nope'), 'x.png')},
                 content_type='multipart/form-data')
    assert bad.status_code == 400
