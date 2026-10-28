"""
First-time account setup: optional-step validation and the Review step
(correction requests vs. finishing setup).

Runs the real Flask routes against the local database, but every query goes
through ONE connection inside a transaction that is rolled back at the end, so
nothing is ever written. A handler's commit() becomes a savepoint and its
rollback() returns to the last "commit", mirroring real transaction behaviour.
"""
import psycopg2
import pytest

from conftest import requires_db

pytestmark = requires_db


class _SharedConn:
    def __init__(self, real):
        self._real = real

    def cursor(self, *a, **k):
        return self._real.cursor(*a, **k)

    def commit(self):
        with self._real.cursor() as c:
            c.execute("SAVEPOINT committed")

    def rollback(self):
        with self._real.cursor() as c:
            c.execute("ROLLBACK TO SAVEPOINT committed")

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    import app as app_module
    import database
    from config import Config

    real = psycopg2.connect(dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
                            host=Config.DB_HOST, port=Config.DB_PORT)
    shared = _SharedConn(real)
    shared.commit()
    monkeypatch.setattr(database, 'get_db_connection', lambda: shared)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: shared)

    cur = real.cursor()
    cur.execute("""
        SELECT a.username, a.role FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.isactive AND a.role IN ('Faculty', 'Academic Head') ORDER BY a.username LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        real.rollback(); real.close()
        pytest.skip("no active faculty account to test with")
    username, role = row
    cur.execute("UPDATE accounts SET account_setup_complete = FALSE, must_change_password = FALSE, "
                "employment_setup_edit_used = FALSE, employment_profile_edit_used = FALSE "
                "WHERE username = %s", (username,))   # a first setup
    cur.execute("DELETE FROM personal_info_correction_request WHERE username = %s", (username,))
    shared.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, username=username, role=role,
                 must_change_password=False, account_setup_complete=False)

    def q(sql, args=()):
        c = real.cursor(); c.execute(sql, args); r = c.fetchall(); c.close(); return r

    def admin_client():
        rows = q("SELECT username FROM accounts WHERE role = 'Admin' AND isactive ORDER BY username LIMIT 1")
        if not rows:
            pytest.skip("no active Admin account to test with")
        c = app_module.app.test_client()
        with c.session_transaction() as s:
            s.update(loggedin=True, username=rows[0][0], role='Admin',
                     must_change_password=False, account_setup_complete=True)
        return c, rows[0][0]

    yield type('Env', (), dict(client=client, username=username, q=staticmethod(q),
                               app=app_module, shared=shared, admin_client=staticmethod(admin_client)))
    real.rollback()
    real.close()


def _complete(env):
    return env.q("SELECT account_setup_complete FROM accounts WHERE username = %s", (env.username,))[0][0]


def _requests(env):
    return env.q("SELECT field_name, proposed_value, remarks, status FROM personal_info_correction_request "
                 "WHERE username = %s ORDER BY requestid", (env.username,))


def _spec_choice(env):
    rows = env.q("SELECT specializationname FROM specialization WHERE isactive ORDER BY specializationname")
    cur = env.q("""SELECT s.specializationname FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
                   LEFT JOIN specialization s ON s.specializationid = f.specializationid
                   WHERE a.username = %s""", (env.username,))[0][0]
    choices = [r[0] for r in rows if r[0] != cur]
    if not choices:
        pytest.skip("need a second active specialization")
    return choices[0]


def _correction(env, items, remarks='Wrong value on file'):
    import json
    return env.client.post('/account-setup/verify', data={
        'action': 'correction', 'corrections_json': json.dumps(items), 'remarks': remarks})


# ---- optional steps: empty Continue is not a Skip --------------------------------

def test_email_empty_continue_stays_and_skip_proceeds(env):
    r = env.client.post('/account-setup/email', data={'action': 'continue', 'email': ''},
                        follow_redirects=True)
    assert b'Please enter an email address, or choose Skip.' in r.data
    r = env.client.post('/account-setup/email', data={'action': 'skip', 'email': ''})
    assert r.headers['Location'].endswith('/account-setup/contact')


def test_email_continue_requires_a_verified_address(env):
    # Continue never saves a typed address: only the emailed-code check does that.
    before = env.q("SELECT a.email FROM accounts a "
                   "WHERE a.username = %s", (env.username,))[0][0]
    r = env.client.post('/account-setup/email', data={'action': 'continue', 'email': 'not.verified@example.com'},
                        follow_redirects=True)
    assert b'Please verify this email address with the code we send, or choose Skip.' in r.data
    after = env.q("SELECT a.email FROM accounts a "
                  "WHERE a.username = %s", (env.username,))[0][0]
    assert after == before
    if before:   # the address already on file counts as verified
        r = env.client.post('/account-setup/email', data={'action': 'continue', 'email': before})
        assert r.headers['Location'].endswith('/account-setup/contact')


def test_step_order_is_password_photo_email_contact_review(env):
    assert [s[0] for s in env.app._SETUP_STEPS] == ['password', 'photo', 'email', 'contact', 'verify']
    r = env.client.post('/account-setup/photo', data={'action': 'skip'})
    assert r.headers['Location'].endswith('/account-setup/email')
    r = env.client.post('/account-setup/email', data={'action': 'back', 'email': ''})
    assert r.headers['Location'].endswith('/account-setup/photo')
    r = env.client.post('/account-setup/contact', data={'action': 'back', 'contact_number': ''})
    assert r.headers['Location'].endswith('/account-setup/email')
    with env.app.app.test_request_context():
        from flask import url_for
        dashboard = url_for('faculty_dashboard')
    r = env.client.get(dashboard)      # unfinished setup resumes at the first optional step
    assert r.headers['Location'].endswith('/account-setup/photo')


def test_contact_empty_continue_stays_and_skip_proceeds(env):
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': ''},
                        follow_redirects=True)
    assert b'Please enter a contact number, or choose Skip.' in r.data
    r = env.client.post('/account-setup/contact', data={'action': 'skip', 'contact_number': ''})
    assert r.headers['Location'].endswith('/account-setup/verify')


# ---- Review step -----------------------------------------------------------------

def test_review_shows_not_provided_for_skipped_fields(env):
    env.q("UPDATE accounts SET contactnumber = NULL WHERE username = %s RETURNING 1", (env.username,))
    env.q("UPDATE accounts SET profile_photo = NULL WHERE username = %s RETURNING 1", (env.username,))
    r = env.client.get('/account-setup/verify')
    assert r.status_code == 200
    assert r.data.count(b'Not provided') >= 2


def test_correction_is_saved_without_finishing_setup(env):
    spec = _spec_choice(env)
    r = _correction(env, [{'field_name': 'Specialization', 'proposed_value': spec}])
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert _requests(env) == [('Specialization', spec, 'Wrong value on file', 'Pending')]
    assert _complete(env) is False
    page = env.client.get('/account-setup/verify').data
    assert b'Correction request submitted for Admin review' in page
    assert b'Waiting for Admin' in page
    with env.client.session_transaction() as s:
        assert s['account_setup_complete'] is False


def test_correction_requires_reason(env):
    spec = _spec_choice(env)
    r = _correction(env, [{'field_name': 'Specialization', 'proposed_value': spec}], remarks='  ')
    page = env.client.get(r.headers['Location']).data
    assert b'Please explain the reason for your correction request.' in page
    assert b'// request-saved' not in page
    assert _requests(env) == []


def test_correction_rejects_value_not_in_list(env):
    _correction(env, [{'field_name': 'Designation', 'proposed_value': 'Made Up Title XYZ'}])
    assert b'Please choose a Designation from the list.' in env.client.get('/account-setup/verify').data
    assert _requests(env) == []


def test_duplicate_pending_correction_is_blocked(env):
    spec = _spec_choice(env)
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': spec}])
    env.client.get('/account-setup/verify')
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': spec}])
    page = env.client.get('/account-setup/verify').data
    assert b'You already have a pending correction request for: Specialization' in page
    assert len(_requests(env)) == 1


def test_confirm_finishes_setup_and_shows_all_set_page_once(env):
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done')
    assert _complete(env) is True
    with env.client.session_transaction() as s:
        assert s['account_setup_complete'] is True
        role = s['role']
    page = env.client.get('/account-setup/done')
    assert page.status_code == 200
    assert b'Great, your account is all set!' in page.data
    with env.app.app.test_request_context():
        from flask import url_for
        dashboard = url_for(env.app._dashboard_url_for_role(role))
    assert f'href="{dashboard}"'.encode() in page.data and b'Go to Dashboard' in page.data
    again = env.client.get('/account-setup/done')         # shown only right after finishing
    assert again.headers['Location'].endswith(dashboard)


def test_contact_requires_a_valid_ph_mobile_number(env):
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '+63 12345'},
                        follow_redirects=True)
    assert b'Please enter a valid Philippine mobile number' in r.data
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '+63 812 345 6789'},
                        follow_redirects=True)
    assert b'Please enter a valid Philippine mobile number' in r.data
    env.q("UPDATE accounts SET contactnumber = NULL WHERE username = %s RETURNING 1", (env.username,))
    def saved():
        return env.q("SELECT a.contactnumber FROM accounts a "
                     "WHERE a.username = %s", (env.username,))[0][0]
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '+63 912 345 6789'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    with env.client.session_transaction() as s:
        assert s['setup_draft']['contact'] == '09123456789'      # same local form as existing records
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '+65 6123 4567'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert saved() is None                   # a draft until Save and Confirm
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert saved() == '+6561234567'


# ---- nothing is saved until Save and Confirm ----------------------------------------

def test_every_setup_page_renders(env):
    _set_default_password(env)
    assert env.client.get('/account-setup/password').status_code == 200
    with env.client.session_transaction() as s:
        s['must_change_password'] = False
    for step in ('photo', 'email', 'contact', 'verify'):
        r = env.client.get(f'/account-setup/{step}')
        assert r.status_code == 200, step


def _pending_password(env):
    return env.q("SELECT passwordhash FROM account_setup_pending WHERE username = %s", (env.username,))


def _set_default_password(env):
    from werkzeug.security import generate_password_hash
    env.q("UPDATE accounts SET passwordhash = %s, must_change_password = TRUE WHERE username = %s RETURNING 1",
          (generate_password_hash('OldPass123'), env.username))
    with env.client.session_transaction() as s:
        s['must_change_password'] = True


def test_new_password_is_kept_as_draft_until_confirm(env):
    from werkzeug.security import check_password_hash
    _set_default_password(env)
    r = env.client.post('/account-setup/password', data={
        'current_password': 'OldPass123', 'new_password': 'NewPass456x', 'confirm_password': 'NewPass456x'})
    assert r.headers['Location'].endswith('/account-setup/photo')
    hash_, must = env.q("SELECT passwordhash, must_change_password FROM accounts WHERE username = %s",
                        (env.username,))[0]
    assert check_password_hash(hash_, 'OldPass123') and must is True       # old password still works
    assert _pending_password(env)
    page = env.client.get('/account-setup/password').data
    assert b'New password set' in page and b'Choose a different password' in page
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done')
    hash_, must = env.q("SELECT passwordhash, must_change_password FROM accounts WHERE username = %s",
                        (env.username,))[0]
    assert check_password_hash(hash_, 'NewPass456x') and must is False
    assert not _pending_password(env)


def test_leave_setup_discards_everything_and_signs_out(env):
    from werkzeug.security import check_password_hash
    _set_default_password(env)
    before = env.q("SELECT contactnumber, profile_photo FROM accounts WHERE username = %s", (env.username,))[0]
    env.client.post('/account-setup/password', data={
        'current_password': 'OldPass123', 'new_password': 'NewPass456x', 'confirm_password': 'NewPass456x'})
    env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '+63 998 765 4321'})
    page = env.client.get('/account-setup/password').data
    assert b'action="/account-setup/leave"' in page
    r = env.client.post('/account-setup/leave')
    assert r.headers['Location'].endswith('/login')
    with env.client.session_transaction() as s:
        assert 'loggedin' not in s and 'setup_draft' not in s
    hash_, must, contact, photo, done = env.q(
        "SELECT passwordhash, must_change_password, contactnumber, profile_photo, account_setup_complete "
        "FROM accounts WHERE username = %s", (env.username,))[0]
    assert check_password_hash(hash_, 'OldPass123') and must is True and done is False
    assert (contact, photo) == before
    assert not _pending_password(env)


def test_review_edit_is_a_draft_until_confirm(env):
    spec = _spec_choice(env)
    def saved_spec():
        return env.q("""SELECT s.specializationname FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
                        LEFT JOIN specialization s ON s.specializationid = f.specializationid
                        WHERE a.username = %s""", (env.username,))[0][0]
    before = saved_spec()
    r = env.client.post('/account-setup/verify', data={'action': 'edit_employment', 'field': 'Specialization',
                                                       'value': spec}, follow_redirects=True)
    assert spec.encode() in r.data and b'when you press Save and Confirm' in r.data
    assert saved_spec() == before
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert saved_spec() == spec and _complete(env) is True


def test_confirm_db_failure_keeps_account_unchanged(env, monkeypatch):
    class _Broken:
        def cursor(self, *a, **k):
            real_cur = env.shared.cursor()
            class C:
                rowcount = 0
                def execute(self, sql, args=()):
                    if 'account_setup_complete = TRUE' in sql:
                        raise psycopg2.OperationalError('simulated outage')
                    return real_cur.execute(sql, args)
                def close(self): real_cur.close()
            return C()
        commit = lambda self: env.shared.commit()
        rollback = lambda self: env.shared.rollback()
        close = lambda self: None
    monkeypatch.setattr(env.app, 'get_db_connection', lambda: _Broken())
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert _complete(env) is False
    with env.client.session_transaction() as s:
        assert s['account_setup_complete'] is False
        assert any('database error' in m for _, m in s.get('_flashes', []))


def test_edit_from_review_returns_to_review(env):
    r = env.client.post('/account-setup/email?from=review', data={'action': 'skip'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    r = env.client.post('/account-setup/email?from=review', data={'action': 'continue', 'email': ''})
    assert r.headers['Location'].endswith('/account-setup/email?from=review')
    r = env.client.post('/account-setup/contact?from=review', data={'action': 'skip'})
    assert r.headers['Location'].endswith('/account-setup/verify')


# ---- Admin: Correction Requests under User Management ---------------------------

def _faculty_spec(env):
    return env.q("""SELECT s.specializationname FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
                    LEFT JOIN specialization s ON s.specializationid = f.specializationid
                    WHERE a.username = %s""", (env.username,))[0][0]


def _pending_id(env, field='Specialization'):
    rows = env.q("SELECT requestid FROM personal_info_correction_request "
                 "WHERE username = %s AND field_name = %s AND status = 'Pending'", (env.username, field))
    return rows[0][0]


def _status(env, rid):
    return env.q("SELECT status FROM personal_info_correction_request WHERE requestid = %s", (rid,))[0][0]


def test_user_accounts_has_correction_requests_button_with_pending_count(env):
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])
    # An already-decided request must not be counted
    env.q("""INSERT INTO personal_info_correction_request (username, field_name, current_value, proposed_value, status)
             VALUES (%s, 'Suffix', '', 'Jr.', 'Rejected') RETURNING 1""", (env.username,))
    admin, _ = env.admin_client()
    pending = env.q("SELECT COUNT(*) FROM personal_info_correction_request WHERE status = 'Pending'")[0][0]
    um = admin.get('/admin/user-management').data.decode()
    # Sidebar: User Management is a plain link to User Accounts, no submenu
    assert 'adminUserSubMenu' not in um and 'Account Corrections</span>' not in um
    assert 'href="/admin/user-management" class="nav-link"' in um
    header = um.split('<div class="page-header">')[1].split('</div>')[0]
    assert 'USER MANAGEMENT' in header and 'href="/admin/account-corrections"' in header
    assert f'<span class="cr-count">{pending}</span>' in header
    # The Correction Requests page: Back link, existing design, sidebar item stays active
    page = admin.get('/admin/account-corrections').data.decode()
    assert 'href="/admin/user-management"><i class="fas fa-arrow-left"></i> Back to User Accounts' in page
    assert 'Review and manage faculty information correction requests.' in page
    assert 'class="nav-item active">\n                        <a href="/admin/user-management"' in page


def test_correction_button_hides_count_when_nothing_pending(env):
    env.q("UPDATE personal_info_correction_request SET status = 'Rejected' WHERE status = 'Pending' RETURNING 1")
    admin, _ = env.admin_client()
    header = admin.get('/admin/user-management').data.decode().split('<div class="page-header">')[1].split('</div>')[0]
    assert 'Correction Requests' in header and 'cr-count' not in header


def test_summary_counts_and_filters(env):
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])
    admin, _ = env.admin_client()
    counts = env.q("""SELECT COUNT(*) FILTER (WHERE status='Pending'), COUNT(*) FILTER (WHERE status='Approved'),
                             COUNT(*) FILTER (WHERE status='Rejected'), COUNT(*)
                      FROM personal_info_correction_request""")[0]
    page = admin.get('/admin/account-corrections?status=All').data.decode()
    for n in counts:
        assert f'<span class="acr-stat-num">{n}</span>' in page
    emp = env.q("SELECT employeenumber FROM accounts WHERE username = %s", (env.username,))[0][0]
    page = admin.get(f'/admin/account-corrections?status=Pending&field=Specialization&q={emp}').data.decode()
    assert f'openReview({_pending_id(env)})' in page
    page = admin.get('/admin/account-corrections?status=Pending&q=zz-no-such-faculty-zz').data.decode()
    assert 'No requests match your search.' in page


def test_admin_approve_updates_faculty_once(env):
    spec = _spec_choice(env)
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': spec}])
    rid = _pending_id(env)
    admin, admin_user = env.admin_client()
    r = admin.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Approved'})
    assert r.status_code == 302
    assert _faculty_spec(env) == spec
    status, by, at = env.q("SELECT status, decided_by, decided_at FROM personal_info_correction_request "
                           "WHERE requestid = %s", (rid,))[0]
    assert (status, by) == ('Approved', admin_user) and at is not None
    # A second decision on the same request is refused on the server
    admin.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Rejected', 'admin_remarks': 'x'})
    assert _status(env, rid) == 'Approved'
    with admin.session_transaction() as s:
        assert any('cannot be processed again' in m for _, m in s.get('_flashes', []))


def test_admin_reject_requires_reason_and_keeps_record(env):
    before = _faculty_spec(env)
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])
    rid = _pending_id(env)
    admin, _ = env.admin_client()
    admin.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Rejected', 'admin_remarks': ' '})
    assert _status(env, rid) == 'Pending'
    admin.post(f'/admin/account-corrections/{rid}/decide',
               data={'decision': 'Rejected', 'admin_remarks': 'Records show the current value is right.'})
    assert env.q("SELECT status, admin_remarks FROM personal_info_correction_request WHERE requestid = %s",
                 (rid,))[0] == ('Rejected', 'Records show the current value is right.')
    assert _faculty_spec(env) == before


def test_admin_cannot_approve_unknown_value(env):
    env.q("""INSERT INTO personal_info_correction_request (username, employeenumber, field_name, current_value,
                 proposed_value, remarks) SELECT username, employeenumber, 'Designation', '', 'No Such Title ZZ', 'r'
             FROM accounts WHERE username = %s RETURNING 1""", (env.username,))
    rid = _pending_id(env, 'Designation')
    admin, _ = env.admin_client()
    admin.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Approved'})
    assert _status(env, rid) == 'Pending'


def test_non_admin_cannot_decide(env):
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])
    rid = _pending_id(env)
    env.client.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Approved'})
    assert _status(env, rid) == 'Pending'


# ---- Faculty: My Correction Requests --------------------------------------------

def test_faculty_sees_only_own_requests(env):
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])
    other = env.q("SELECT username FROM accounts WHERE username <> %s LIMIT 1", (env.username,))[0][0]
    env.q("""INSERT INTO personal_info_correction_request (username, field_name, current_value, proposed_value)
             VALUES (%s, 'Suffix', '', 'Jr.') RETURNING 1""", (other,))
    d = env.client.get('/api/user/correction-requests').get_json()
    assert d['success'] and [r['field'] for r in d['requests']] == ['Specialization']
    assert d['requests'][0]['status'] == 'Pending' and d['requests'][0]['reason'] == 'Wrong value on file'
    assert env.app.app.test_client().get('/api/user/correction-requests').status_code == 401


def test_my_correction_requests_lives_in_profile_settings_after_setup(env):
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])
    env.client.post('/account-setup/verify', data={'action': 'confirm'})          # finish setup
    with env.client.session_transaction() as s:
        role = s['role']
    home = '/faculty_dashboard' if role == 'Faculty' else '/academic/my-dashboard'
    page = env.client.get(home, follow_redirects=True).data.decode()
    profile = page.split('id="profilePanel"')[1].split('id="emailPanel"')[0]
    assert 'id="crCard"' in profile and 'My Correction Requests' in profile    # inside Profile Settings
    assert '<span class="cr-status cr-Pending">1 pending</span>' in profile
    assert 'correctionsPanel' not in page and 'openCorrectionsPanel' not in page   # no separate menu item
    assert 'class="cr-dot"' in page                                               # indicator on Profile
    assert env.client.get('/api/user/correction-requests').get_json()['requests'][0]['status'] == 'Pending'
    admin, _ = env.admin_client()
    assert 'id="crCard"' not in admin.get('/admin/user-management').data.decode()


def test_faculty_sees_admin_decisions(env):
    spec = _spec_choice(env)
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': spec}])
    admin, _ = env.admin_client()
    admin.post(f'/admin/account-corrections/{_pending_id(env)}/decide', data={'decision': 'Approved'})
    _correction(env, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])   # a 2nd request
    admin.post(f'/admin/account-corrections/{_pending_id(env)}/decide',
               data={'decision': 'Rejected', 'admin_remarks': 'Not supported by HR records.'})
    reqs = env.client.get('/api/user/correction-requests').get_json()['requests']
    assert sorted(r['status'] for r in reqs) == ['Approved', 'Rejected']
    rejected = next(r for r in reqs if r['status'] == 'Rejected')
    assert rejected['admin_remarks'] == 'Not supported by HR records.' and rejected['decided']
    assert _faculty_spec(env) == spec                     # the rejection left the approved value in place


# ---- Profile Settings: Request Correction (after setup) ---------------------------

def _post_correction(client, items, remarks='Wrong value on file'):
    return client.post('/api/user/correction-requests', json={'items': items, 'remarks': remarks})


def test_profile_correction_request_is_saved_for_admin_review(env):
    spec = _spec_choice(env)
    r = _post_correction(env.client, [{'field_name': 'Specialization', 'proposed_value': spec}])
    assert r.status_code == 200 and r.get_json()['success'] and r.get_json()['count'] == 1
    assert _requests(env) == [('Specialization', spec, 'Wrong value on file', 'Pending')]
    # The faculty record is untouched until the Admin approves it.
    assert env.q("""SELECT s.specializationname FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
                    LEFT JOIN specialization s ON s.specializationid = f.specializationid
                    WHERE a.username = %s""", (env.username,))[0][0] != spec


def test_profile_correction_request_uses_the_same_rules(env):
    spec = _spec_choice(env)
    r = _post_correction(env.client, [{'field_name': 'Specialization', 'proposed_value': spec}], remarks=' ')
    assert r.status_code == 400 and 'explain the reason' in r.get_json()['error']
    r = _post_correction(env.client, [{'field_name': 'Designation', 'proposed_value': 'Made Up Title XYZ'}])
    assert r.status_code == 400 and 'from the list' in r.get_json()['error']
    assert _post_correction(env.client, [{'field_name': 'Specialization', 'proposed_value': spec}]).status_code == 200
    r = _post_correction(env.client, [{'field_name': 'Specialization', 'proposed_value': spec}])
    assert r.status_code == 400 and 'already have a pending correction request for: Specialization' in r.get_json()['error']
    assert len(_requests(env)) == 1


def test_profile_correction_request_needs_a_faculty_record(env):
    admin, _ = env.admin_client()
    r = _post_correction(admin, [{'field_name': 'Specialization', 'proposed_value': 'X'}])
    assert r.status_code == 400 and 'faculty record' in r.get_json()['error']
    assert env.app.app.test_client().post('/api/user/correction-requests', json={}).status_code == 401


def test_profile_settings_has_the_request_correction_button(env):
    env.client.post('/account-setup/verify', data={'action': 'confirm'})          # finish setup
    with env.client.session_transaction() as s:
        role = s['role']
    home = '/faculty_dashboard' if role == 'Faculty' else '/academic/my-dashboard'
    page = env.client.get(home, follow_redirects=True).data.decode()
    profile = page.split('id="profilePanel"')[1].split('id="emailPanel"')[0]
    assert 'id="pfRequestCorrectionBtn"' in profile and 'openCorrectionModal()' in profile
    assert 'id="crModal"' in page
    admin, _ = env.admin_client()
    admin_page = admin.get('/admin/user-management').data.decode()
    assert 'pfRequestCorrectionBtn' not in admin_page and 'id="crModal"' not in admin_page


# ---- Academic Information: name, contact number, one-time employment edit ----------

def test_employment_is_editable_only_in_the_first_setup(env):
    spec = _spec_choice(env)
    env.q("UPDATE accounts SET employment_setup_edit_used = TRUE WHERE username = %s RETURNING 1", (env.username,))
    page = env.client.post('/account-setup/verify', data={'action': 'edit_employment', 'field': 'Specialization',
                                                          'value': spec}, follow_redirects=True).data
    assert b'already used the account-setup edit' in page
    with env.client.session_transaction() as s:
        assert not (s.get('setup_draft') or {}).get('faculty')
    assert b'SETUP EDIT USED' in env.client.get('/account-setup/verify').data


def test_first_setup_confirm_uses_up_the_one_time_edit(env):
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert env.q("SELECT employment_setup_edit_used FROM accounts WHERE username = %s",
                 (env.username,))[0][0] is True


def test_name_is_saved_right_away(env):
    r = env.client.post('/api/user/name', json={'First Name': 'Rosario', 'Middle Name': '',
                                                'Last Name': 'Anulaoskie', 'Suffix': 'Jr.'})
    assert r.get_json()['success']
    first, middle, last, suffix = env.q(
        "SELECT f.firstname, f.middlename, f.lastname, f.suffix FROM accounts a "
        "JOIN faculty f ON f.employeenumber = a.employeenumber WHERE a.username = %s", (env.username,))[0]
    assert (first, middle, last, suffix) == ('Rosario', None, 'Anulaoskie', 'Jr.')
    r = env.client.post('/api/user/name', json={'First Name': '', 'Last Name': 'X'})
    assert r.status_code == 400 and 'First Name' in r.get_json()['error']


def test_name_is_no_longer_a_correction_request(env):
    r = _post_correction(env.client, [{'field_name': 'Last Name', 'proposed_value': 'Other'}])
    assert r.status_code == 400 and _requests(env) == []


def test_employment_type_and_status_corrections_are_applied_on_approval(env):
    cur_type, cur_status = env.q("""SELECT et.typename, f.employeestatus FROM accounts a
                                    JOIN faculty f ON f.employeenumber = a.employeenumber
                                    JOIN employeetype et ON et.employeetypeid = f.employeetypeid
                                    WHERE a.username = %s""", (env.username,))[0]
    new_type = next(t for (t,) in env.q("SELECT typename FROM employeetype ORDER BY employeetypeid") if t != cur_type)
    new_status = next(s for s in ('Permanent', 'Temporary', 'Part-Time') if s != cur_status)
    r = _post_correction(env.client, [{'field_name': 'Employment Type', 'proposed_value': new_type},
                                      {'field_name': 'Employment Status', 'proposed_value': new_status}])
    assert r.get_json()['count'] == 2
    admin, _ = env.admin_client()
    for field in ('Employment Type', 'Employment Status'):
        admin.post(f'/admin/account-corrections/{_pending_id(env, field)}/decide', data={'decision': 'Approved'})
    assert env.q("""SELECT et.typename, f.employeestatus FROM accounts a
                    JOIN faculty f ON f.employeenumber = a.employeenumber
                    JOIN employeetype et ON et.employeetypeid = f.employeetypeid
                    WHERE a.username = %s""", (env.username,))[0] == (new_type, new_status)


def test_contact_number_changes_only_with_the_emailed_code(env, monkeypatch):
    import contact_change
    sent = []
    monkeypatch.setattr(contact_change.mailer, 'send_email', lambda cfg, to, subj, text, html: sent.append(text))
    monkeypatch.setattr(contact_change, 'smtp_configured', lambda cfg: True)
    env.q("UPDATE accounts SET email = %s, contactnumber = NULL WHERE username = %s RETURNING 1",
          (f'{env.username}.test@example.com', env.username))
    r = env.client.post('/api/user/contact/request-code', data={'contact': '+63 917 000 1234'}).get_json()
    assert r['success'] and r['contact'] == '09170001234'
    code = sent[0].split('code is: ')[1][:6]
    def saved():
        return env.q("SELECT contactnumber FROM accounts WHERE username = %s", (env.username,))[0][0]
    wrong = env.client.post('/api/user/contact/update', data={'contact': '+63 917 000 1234',
                                                              'code': '000000' if code != '000000' else '111111'})
    assert wrong.status_code == 400 and saved() is None
    ok = env.client.post('/api/user/contact/update', data={'contact': '+63 917 000 1234', 'code': code}).get_json()
    assert ok['success'] and saved() == '09170001234'


def test_academic_information_panel_layout(env):
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    with env.client.session_transaction() as s:
        role = s['role']
    home = '/faculty_dashboard' if role == 'Faculty' else '/academic/my-dashboard'
    page = env.client.get(home, follow_redirects=True).data.decode()
    profile = page.split('id="profilePanel"')[1].split('id="emailPanel"')[0]
    assert 'Academic Information' in profile and 'Profile Settings' not in profile
    assert 'Click the camera button to choose a photo from your files or take one' in profile
    assert 'onclick="pfChooseFile()"' in profile and 'onclick="pfTakePhoto()"' in profile
    assert 'id="pfNameEditBtn"' in profile and 'id="pfContactBtn"' in profile
    assert 'Official Employment Information' in profile and 'Sign-in Details' not in profile
    for label in ('Employee ID', 'Specialization', 'Employment Type', 'Employment Status'):
        assert f'<div class="pf-emp-label">{label}</div>' in profile


def test_wrong_current_password_keeps_what_was_typed(env):
    _set_default_password(env)
    r = env.client.post('/account-setup/password', data={
        'current_password': 'EMP003', 'new_password': 'NewPass456x', 'confirm_password': 'NewPass456x'})
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and 'Current password is incorrect.' in page
    for typed in ('value="EMP003"', 'value="NewPass456x"'):
        assert typed in page
    assert not _pending_password(env)


# ---- Employee ID changes: the new ID is also the username ------------------------

def _emp(env, username=None):
    rows = env.q("SELECT employeenumber FROM accounts WHERE username = %s", (username or env.username,))
    return rows[0][0] if rows else None


def _ref_counts(env, emp):
    return {t: env.q(f"SELECT COUNT(*) FROM {t} WHERE employeenumber = %s", (emp,))[0][0]
            for t in ('schedule', 'mergedclass', 'schedule_version', 'subject_faculty_assignment')}


def test_setup_employee_id_change_becomes_the_username(env):
    import uuid
    old = _emp(env)
    before = _ref_counts(env, old)
    new = 'T-' + uuid.uuid4().hex[:8].upper()
    page = env.client.post('/account-setup/verify', data={'action': 'edit_employee_id', 'value': new},
                           follow_redirects=True).get_data(as_text=True)
    assert 'your username also becomes' in page and new in page          # the notice before confirming
    assert _emp(env) == old                                              # still only a draft
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done')
    assert env.q("SELECT 1 FROM accounts WHERE username = %s", (env.username,)) == []
    assert _emp(env, new) == new                                         # username and Employee ID
    assert env.q("SELECT COUNT(*) FROM faculty WHERE employeenumber = %s", (old,))[0][0] == 0
    assert _ref_counts(env, new) == before and sum(_ref_counts(env, old).values()) == 0
    with env.client.session_transaction() as s:
        assert s['username'] == new
    done = env.client.get('/account-setup/done').get_data(as_text=True)
    assert f'your username is now <strong>{new}</strong>' in done


def test_employee_id_already_used_is_rejected(env):
    other = env.q("SELECT employeenumber FROM faculty WHERE employeenumber <> %s LIMIT 1", (_emp(env),))[0][0]
    page = env.client.post('/account-setup/verify', data={'action': 'edit_employee_id', 'value': other},
                           follow_redirects=True).data
    assert b'already belongs to another account' in page


def test_one_employment_edit_in_academic_information(env):
    env.client.post('/account-setup/verify', data={'action': 'confirm'})          # finish setup
    assert env.client.get('/api/user/me').get_json()['employment_edit_available'] is True
    spec = _spec_choice(env)
    r = env.client.post('/api/user/employment', json={'Specialization': spec})
    assert r.get_json()['success'] and _faculty_spec(env) == spec
    me = env.client.get('/api/user/me').get_json()
    assert me['employment_edit_available'] is False
    r = env.client.post('/api/user/employment', json={'Specialization': _spec_choice(env)})
    assert r.status_code == 403 and 'Request Correction' in r.get_json()['error']


def test_academic_information_employee_id_edit_renames_the_account(env):
    import uuid
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    new = 'T-' + uuid.uuid4().hex[:8].upper()
    data = env.client.post('/api/user/employment', json={'Employee ID': new}).get_json()
    assert data['success'] and data['new_username'] == new
    assert _emp(env, new) == new
    assert env.client.get('/api/user/me').get_json()['emp_num'] == new          # session follows


def _home(env):
    with env.client.session_transaction() as s:
        role = s['role']
    return '/faculty_dashboard' if role == 'Faculty' else '/academic/my-dashboard'


def _approve_employee_id_request(env, new):
    r = _post_correction(env.client, [{'field_name': 'Employee ID', 'proposed_value': new}])
    assert r.get_json()['success']
    admin, _ = env.admin_client()
    listing = admin.get('/admin/account-corrections').get_data(as_text=True)
    assert 'Also changes their username' in listing and 'is also this faculty member' in listing
    return admin.post(f'/admin/account-corrections/{_pending_id(env, "Employee ID")}/decide',
                      data={'decision': 'Approved'}, follow_redirects=True).get_data(as_text=True)


def test_approved_employee_id_waits_until_the_user_confirms_it(env):
    import uuid
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    old, new = _emp(env), 'T-' + uuid.uuid4().hex[:8].upper()
    page = _approve_employee_id_request(env, new)
    assert 'takes effect once' in page and 'confirms it' in page
    assert _emp(env) == old                                    # approved, but nothing changed yet
    reqs = env.client.get('/api/user/correction-requests').get_json()['requests']
    assert reqs[0]['status'] == 'Approved' and reqs[0]['applied'] is False
    # Signed in with the old username, the faculty member sees the notice...
    home = env.client.get(_home(env), follow_redirects=True).get_data(as_text=True)
    assert f'id="approvedIdNotice" data-new-id="{new}"' in home
    # ...and no second Employee ID change can start meanwhile.
    r = _post_correction(env.client, [{'field_name': 'Employee ID', 'proposed_value': 'T-OTHER-1'}])
    assert r.status_code == 400 and 'waiting for you to confirm' in r.get_json()['error']
    # Got It: only now does the new ID / username take effect.
    data = env.client.post('/api/user/employee-id/accept').get_json()
    assert data['success'] and data['new_username'] == new
    assert _emp(env, new) == new and env.q("SELECT 1 FROM accounts WHERE username = %s", (old,)) == []
    with env.client.session_transaction() as s:
        assert s['username'] == new
    assert env.client.get('/api/user/me').get_json()['emp_num'] == new
    reqs = env.client.get('/api/user/correction-requests').get_json()['requests']
    assert reqs[0]['applied'] is True
    home = env.client.get(_home(env), follow_redirects=True).get_data(as_text=True)
    assert 'id="approvedIdNotice"' not in home                # shown only until confirmed
    assert env.client.post('/api/user/employee-id/accept').status_code == 400


def test_rejected_employee_id_request_changes_nothing(env):
    import uuid
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    old, new = _emp(env), 'T-' + uuid.uuid4().hex[:8].upper()
    _post_correction(env.client, [{'field_name': 'Employee ID', 'proposed_value': new}])
    admin, _ = env.admin_client()
    admin.post(f'/admin/account-corrections/{_pending_id(env, "Employee ID")}/decide',
               data={'decision': 'Rejected', 'admin_remarks': 'Not on HR records.'})
    assert _emp(env) == old
    home = env.client.get(_home(env), follow_redirects=True).get_data(as_text=True)
    assert 'id="approvedIdNotice"' not in home
    assert env.client.post('/api/user/employee-id/accept').status_code == 400


def test_other_approved_corrections_still_apply_right_away(env):
    spec = _spec_choice(env)
    _post_correction(env.client, [{'field_name': 'Specialization', 'proposed_value': spec}])
    admin, _ = env.admin_client()
    admin.post(f'/admin/account-corrections/{_pending_id(env)}/decide', data={'decision': 'Approved'})
    assert _faculty_spec(env) == spec
    reqs = env.client.get('/api/user/correction-requests').get_json()['requests']
    assert reqs[0]['applied'] is True


def test_admin_password_reset_does_not_restart_a_finished_setup(env):
    env.client.post('/account-setup/verify', data={'action': 'confirm'})              # setup finished
    assert _complete(env) is True
    admin, _ = env.admin_client()
    userid = env.q("SELECT userid FROM accounts WHERE username = %s", (env.username,))[0][0]
    admin.post('/admin/accounts/reset_password', data={'user_id': userid, 'username': env.username})
    c = env.app.app.test_client()
    r = c.post('/login', data={'username': env.username, 'password': f'PUP@{env.username}'})
    r = c.get(r.headers['Location'])                                  # the dashboard sends them on
    assert r.headers['Location'].endswith('/account-setup/password')
    page = c.get('/account-setup/password').get_data(as_text=True)
    assert 'Change your password' in page and 'Step 1 of' not in page and 'Leave Account Setup?' not in page
    r = c.post('/account-setup/password', data={'current_password': f'PUP@{env.username}',
                                                'new_password': 'NewPass456x', 'confirm_password': 'NewPass456x'})
    assert '/account-setup/' not in r.headers['Location']                                # straight to the dashboard
    assert _complete(env) is True
    assert env.q("SELECT must_change_password FROM accounts WHERE username = %s", (env.username,))[0][0] is False


def test_default_password_login_on_finished_account_only_asks_for_password(env):
    from werkzeug.security import generate_password_hash
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    env.q("UPDATE accounts SET passwordhash = %s, must_change_password = FALSE WHERE username = %s RETURNING 1",
          (generate_password_hash(env.username), env.username))
    c = env.app.app.test_client()
    r = c.post('/login', data={'username': env.username, 'password': env.username})
    r = c.get(r.headers['Location'])                                  # the dashboard sends them on
    assert r.headers['Location'].endswith('/account-setup/password')
    assert _complete(env) is True


def test_forgot_password_reset_unlocks_a_locked_account(env, monkeypatch):
    import login_security as ls
    import password_reset as pr
    from config import Config
    env.client.post('/account-setup/verify', data={'action': 'confirm'})              # a finished account
    env.q("UPDATE accounts SET email = %s WHERE username = %s RETURNING 1",
          (f'{env.username}.reset@example.com', env.username))
    ip = '203.0.113.77'
    env.q("DELETE FROM login_ip_failures WHERE ip = %s RETURNING 1", (ip,))
    c = env.app.app.test_client()
    for _ in range(ls.MAX_FAILED_ATTEMPTS):
        c.post('/login', data={'username': env.username, 'password': 'wrong'}, environ_base={'REMOTE_ADDR': ip})
    assert env.q("SELECT locked_until IS NOT NULL FROM login_throttle WHERE username_key = %s",
                 (ls.username_key(env.username),))[0][0] is True
    codes = []
    monkeypatch.setattr(pr, 'send_reset_email', lambda cfg, to, code: codes.append(code))
    rid, job = pr.start(env.shared, Config, env.username, ip)
    job()
    token = pr.verify(env.shared, rid, codes[0])                                     # email code verified
    pr.reset(env.shared, rid, token, 'BrandNew789x', 'BrandNew789x')
    assert env.q("SELECT failed_count, locked_until FROM login_throttle WHERE username_key = %s",
                 (ls.username_key(env.username),))[0] == (0, None)                   # unlocked automatically
    r = c.post('/login', data={'username': env.username, 'password': 'BrandNew789x'},
               environ_base={'REMOTE_ADDR': ip})
    assert '/login' not in r.headers['Location']                                     # signed in right away


def test_forgot_password_uses_the_account_setup_layout(env):
    c = env.app.app.test_client()
    page = c.get('/forgot-password').get_data(as_text=True)
    assert 'class="setup-shell"' in page and 'class="setup-steps"' in page and 'Step 1 of 3' in page
    assert '<label for="fpIdent">Employee Number</label>' in page and 'Username' not in page
    r = c.post('/forgot-password', data={'identifier': 'NO-SUCH-EMP-123'}, environ_base={'REMOTE_ADDR': '203.0.113.79'})
    page = c.get(r.headers['Location']).get_data(as_text=True)
    assert 'Step 2 of 3' in page and 'class="alert-info"' in page and 'If the Employee Number you entered' in page


def test_account_marked_complete_but_never_set_up_gets_the_full_setup(env):
    from werkzeug.security import generate_password_hash
    # Imported / pre-wizard account: marked complete, but never set its own password.
    env.q("""UPDATE accounts SET passwordhash = %s, must_change_password = FALSE, account_setup_complete = TRUE,
                                 password_changed_at = NULL, setup_completed_at = NULL
             WHERE username = %s RETURNING 1""", (generate_password_hash(env.username), env.username))
    c = env.app.app.test_client()
    r = c.post('/login', data={'username': env.username, 'password': env.username})
    r = c.get(r.headers['Location'])
    assert r.headers['Location'].endswith('/account-setup/password')
    page = c.get('/account-setup/password').get_data(as_text=True)
    assert 'Step 1 of' in page and 'Change your password' not in page            # the real setup
    assert _complete(env) is False
    r = c.post('/account-setup/password', data={'current_password': env.username,
                                                'new_password': 'NewPass456x', 'confirm_password': 'NewPass456x'})
    assert r.headers['Location'].endswith('/account-setup/photo')                 # step 2, not the dashboard


def test_admin_sidebar_shows_pending_request_count(env):
    import re
    admin, _ = env.admin_client()
    before = env.q("SELECT COUNT(*) FROM personal_info_correction_request WHERE status = 'Pending'")[0][0]
    _post_correction(env.client, [{'field_name': 'Specialization', 'proposed_value': _spec_choice(env)}])
    page = admin.get('/admin/dashboard', follow_redirects=True).get_data(as_text=True)
    m = re.search(r'<span class="nav-badge"[^>]*>(\d+\+?)</span>', page)
    assert m and m.group(1) == str(before + 1)


def test_signing_in_with_a_changed_employee_id_says_so(env):
    import uuid
    import login_security as ls
    env.client.post('/account-setup/verify', data={'action': 'confirm'})
    old, new = _emp(env), 'T-' + uuid.uuid4().hex[:8].upper()
    env.client.post('/api/user/employment', json={'Employee ID': new})              # renamed (recorded)
    c = env.app.app.test_client()
    for _ in range(ls.MAX_FAILED_ATTEMPTS + 1):
        c.post('/login', data={'username': old.lower(), 'password': 'whatever'},
               environ_base={'REMOTE_ADDR': '203.0.113.81'})
    page = c.get('/login').get_data(as_text=True)
    assert 'This Employee ID was changed' in page and new not in page             # the new ID is not revealed
    assert 'Warning:' not in page and 'class="lock-box"' not in page              # not counted towards a lock
    assert env.q("SELECT COUNT(*) FROM login_throttle WHERE username_key = %s",
                 (ls.username_key(old),))[0][0] == 0


def test_faculty_can_permanently_delete_only_cancelled_or_rejected_requests(env):
    import pytest
    rows = env.q("""SELECT r.requestid, r.status, a.username FROM schedule_change_request r
                    JOIN accounts a ON a.employeenumber = r.submitted_by AND a.role = 'Faculty'
                    WHERE r.status IN ('Cancelled', 'Pending', 'Approved') ORDER BY r.requestid""")
    done = next((r for r in rows if r[1] == 'Cancelled'), None)
    if not done:
        pytest.skip("no cancelled adjustment request to test with")
    c = env.app.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=done[2], role='Faculty',
                 must_change_password=False, account_setup_complete=True)
    page = c.get('/faculty_requests').get_data(as_text=True)
    assert 'function rfAskDelete' in page
    for other in (r for r in rows if r[1] in ('Pending', 'Approved') and r[2] == done[2]):
        r = c.delete(f'/api/faculty/my_requests/adjustment/{other[0]}/permanent')
        assert r.status_code == 400 and 'cancelled or rejected' in r.get_json()['error']
    r = c.delete(f'/api/faculty/my_requests/adjustment/{done[0]}/permanent')
    assert r.get_json()['success']
    assert env.q("SELECT 1 FROM schedule_change_request WHERE requestid = %s", (done[0],)) == []
