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
    cur.execute("UPDATE accounts SET account_setup_complete = FALSE, must_change_password = FALSE "
                "WHERE username = %s", (username,))
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
    before = env.q("SELECT f.email FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber "
                   "WHERE a.username = %s", (env.username,))[0][0]
    r = env.client.post('/account-setup/email', data={'action': 'continue', 'email': 'not.verified@example.com'},
                        follow_redirects=True)
    assert b'Please verify this email address with the code we send, or choose Skip.' in r.data
    after = env.q("SELECT f.email FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber "
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
    env.q("UPDATE faculty SET contactnumber = NULL WHERE employeenumber = "
          "(SELECT employeenumber FROM accounts WHERE username = %s) RETURNING 1", (env.username,))
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
    assert b'Pending review' in page
    assert b'// request-saved' in page            # unsent list is cleared only after a save
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
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '+63 912 345 6789'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    def saved():
        return env.q("SELECT f.contactnumber FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber "
                     "WHERE a.username = %s", (env.username,))[0][0]
    assert saved() == '09123456789'          # same local form as existing records; fits varchar(15)
    r = env.client.post('/account-setup/contact', data={'action': 'continue', 'contact_number': '+65 6123 4567'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert saved() == '+6561234567'


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
