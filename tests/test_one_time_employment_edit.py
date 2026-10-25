"""
Account Setup Review + Profile Settings editing permissions:
  - Name: editable any time. (Contact Number: any time, with an emailed code --
    see test_contact_change_otp.py.)
  - Employee ID / Designation / Specialization / Employment Type / Employment Status:
    edited only on the Account Setup Review step (no limit). Those edits are kept with the
    setup and saved -- logged 'setup_edit' -- only by Save and Confirm.
    In Profile Settings they are read-only and change only through an Admin-approved
    correction request -- there is no direct-edit endpoint.

Runs the real Flask routes against the local database on ONE connection inside a
transaction that is rolled back at the end (same pattern as test_account_setup_review).
migrations/2026-10-22_one_time_employment_edit.sql is applied inside that transaction
too, so the tests work whether or not it has been applied, and never persist it.
"""
import os
import re

import psycopg2
import pytest

from conftest import requires_db

pytestmark = requires_db

_MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'migrations', '2026-10-22_one_time_employment_edit.sql')


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
    cur = real.cursor()
    with open(_MIGRATION, encoding='utf-8') as f:
        cur.execute(f.read())
    shared = _SharedConn(real)
    shared.commit()
    monkeypatch.setattr(database, 'get_db_connection', lambda: shared)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: shared)

    cur.execute("""
        SELECT a.username, a.role, a.employeenumber FROM accounts a
        JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.isactive AND a.role IN ('Faculty', 'Academic Head') ORDER BY a.username LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        real.rollback(); real.close()
        pytest.skip("no active faculty account to test with")
    username, role, emp = row
    cur.execute("UPDATE accounts SET account_setup_complete = FALSE, must_change_password = FALSE "
                "WHERE username = %s", (username,))
    cur.execute("DELETE FROM personal_info_correction_request WHERE username = %s", (username,))
    cur.execute("DELETE FROM faculty_field_change_log WHERE employeenumber = %s", (emp,))
    cur.execute("DELETE FROM account_setup_draft WHERE username = %s", (username,))
    # A known starting point: a Regular faculty member (so no Designation).
    cur.execute("""UPDATE faculty SET designationid = NULL,
                          employeetypeid = (SELECT employeetypeid FROM employeetype WHERE typename = 'Regular')
                   WHERE employeenumber = %s""", (emp,))
    shared.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, username=username, role=role,
                 must_change_password=False, account_setup_complete=False)

    def q(sql, args=()):
        c = real.cursor(); c.execute(sql, args)
        r = c.fetchall() if c.description else []
        c.close(); return r

    def admin_client():
        rows = q("SELECT username FROM accounts WHERE role = 'Admin' AND isactive ORDER BY username LIMIT 1")
        if not rows:
            pytest.skip("no active Admin account to test with")
        c = app_module.app.test_client()
        with c.session_transaction() as s:
            s.update(loggedin=True, username=rows[0][0], role='Admin',
                     must_change_password=False, account_setup_complete=True)
        return c, rows[0][0]

    yield type('Env', (), dict(client=client, username=username, emp=emp, q=staticmethod(q),
                               app=app_module, shared=shared, admin_client=staticmethod(admin_client)))
    real.rollback()
    real.close()


# ---- helpers --------------------------------------------------------------------

def _current(env, field):
    col = {'Specialization': 's.specializationname', 'Employment Type': 'et.typename',
           'Employment Status': 'f.employeestatus', 'First Name': 'f.firstname'}[field]
    return env.q(f"""SELECT {col} FROM faculty f
                     LEFT JOIN specialization s ON s.specializationid = f.specializationid
                     LEFT JOIN employeetype et ON et.employeetypeid = f.employeetypeid
                     WHERE f.employeenumber = %s""", (env.emp,))[0][0] or ''


def _other_value(env, field):
    if field == 'Employment Status':
        options = list(env.app._EMPLOYMENT_STATUSES)
    elif field == 'Employment Type':           # Designee needs a Designation: see the Designation tests
        options = [r[0] for r in env.q("SELECT typename FROM employeetype WHERE typename <> 'Designee' "
                                       "ORDER BY typename")]
    else:
        options = [r[0] for r in env.q("SELECT specializationname FROM specialization WHERE isactive "
                                       "ORDER BY specializationname")]
    choices = [o for o in options if o != _current(env, field)]
    if not choices:
        pytest.skip(f"need a second {field} value")
    return choices[0]


def _setup_edit(env, field, value):
    return env.client.post('/account-setup/verify',
                           data={'action': 'edit_employment', 'field': field, 'value': value})


def _profile_edit(env, field, value):
    """What the removed Profile Settings direct edit used to accept."""
    return env.client.post('/api/user/profile/employment', data={'field': field, 'value': value})


def _self_edits(env, field=None):
    """Profile Settings direct edits (name only now)."""
    sql = ("SELECT field_name, old_value, new_value, changed_by FROM faculty_field_change_log "
           "WHERE employeenumber = %s AND change_source = 'self_edit'")
    args = [env.emp]
    if field:
        sql += " AND field_name = %s"; args.append(field)
    return env.q(sql + " ORDER BY changeid", tuple(args))


def _setup_edits(env, field=None):
    sql = ("SELECT field_name, old_value, new_value, changed_by FROM faculty_field_change_log "
           "WHERE employeenumber = %s AND change_source = 'setup_edit'")
    args = [env.emp]
    if field:
        sql += " AND field_name = %s"; args.append(field)
    return env.q(sql + " ORDER BY changeid", tuple(args))


def _flashes(client):
    with client.session_transaction() as s:
        return [m for _, m in s.get('_flashes', [])]


def _confirm(env):
    """Save and Confirm on Step 5."""
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done'), _flashes(env.client)


def _finish_setup(env):
    env.q("UPDATE accounts SET account_setup_complete = TRUE WHERE username = %s RETURNING 1", (env.username,))
    with env.client.session_transaction() as sess:
        sess['account_setup_complete'] = True


# ---- Profile Settings: official employment information is read-only -------------------

def test_profile_settings_cannot_change_official_information_directly(env):
    _finish_setup(env)
    before = {f: _current(env, f) for f in env.app._ONE_TIME_FIELDS}
    for field in env.app._ONE_TIME_FIELDS:
        r = _profile_edit(env, field, _other_value(env, field))
        assert r.status_code in (404, 405), field
    for data in ({'field': 'Employment Type', 'value': 'Designee', 'designation': 'x'},
                 {'field': 'Employment Type', 'value': 'Regular', 'clear_designation': '1'}):
        assert env.client.post('/api/user/profile/employment', data=data).status_code in (404, 405)
    # Nor through the Account Setup Review step once setup is complete.
    r = _setup_edit(env, 'Specialization', _other_value(env, 'Specialization'))
    assert r.status_code == 302 and '/account-setup/verify' not in r.headers['Location']
    assert {f: _current(env, f) for f in env.app._ONE_TIME_FIELDS} == before
    assert env.q("SELECT COUNT(*) FROM faculty_field_change_log WHERE employeenumber = %s", (env.emp,))[0][0] == 0


def test_review_edits_are_refused_once_setup_is_complete(env):
    _finish_setup(env)
    value = _other_value(env, 'Specialization')
    for data in ({'action': 'edit_employment', 'field': 'Specialization', 'value': value},
                 {'action': 'edit_employee_id', 'value': 'NOPE-0001'},
                 {'action': 'edit_designation', 'value': ''}):
        r = env.client.post('/account-setup/verify', data=data)
        assert r.status_code == 302 and '/account-setup/' not in r.headers['Location']
    assert _current(env, 'Specialization') != value
    assert env.q("SELECT 1 FROM account_setup_draft WHERE username = %s", (env.username,)) == []


def test_profile_settings_shows_read_only_rows_and_keeps_request_correction(env):
    _finish_setup(env)
    with env.client.session_transaction() as sess:
        role = sess['role']
    home = '/faculty_dashboard' if role == 'Faculty' else '/academic/my-dashboard'
    page = env.client.get(home, follow_redirects=True).data.decode()
    profile = page.split('id="profilePanel"')[1].split('id="emailPanel"')[0]
    assert 'Official Employment Information' in profile and 'id="pfRequestCorrectionBtn"' in profile
    assert 'once each' not in profile and 'pfOneTimeNote' not in profile
    me = env.client.get('/api/user/me').get_json()
    assert 'edit_allowed' not in me and 'edit_ready' not in me and me['pending_fields'] == []
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'static', 'js', 'profile.dropdown.js'), encoding='utf-8').read()
    assert '/api/user/profile/employment' not in js and 'savePfEmployment' not in js
    # Every official field is offered in the correction form.
    assert "_PF_EMPLOYMENT.map(f =>\n            _crmItemHtml(" in js


# ---- Account Setup: free direct edits ---------------------------------------------------

def test_setup_edits_are_kept_until_save_and_confirm(env):
    old = _current(env, 'Employment Status')
    new = _other_value(env, 'Employment Status')
    r = _setup_edit(env, 'Employment Status', new)
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert _current(env, 'Employment Status') == old                    # not saved yet
    assert f'<div class="of-value ">{new}</div>' in env.client.get('/account-setup/verify').data.decode()
    _setup_edit(env, 'Employment Status', old)                          # no limit during setup
    _setup_edit(env, 'Employment Status', new)
    assert _current(env, 'Employment Status') == old and _setup_edits(env) == []
    _confirm(env)
    assert _current(env, 'Employment Status') == new
    assert _setup_edits(env) == [('Employment Status', old, new, env.username)]
    assert env.q("SELECT account_setup_complete FROM accounts WHERE username = %s", (env.username,))[0][0] is True


def test_step5_has_no_read_only_or_correction_labels(env):
    page = env.client.get('/account-setup/verify').data.decode()
    assert 'toggleEdit("ed-employment_status", true)' in page
    for gone in ('Read-only', 'READ-ONLY', '1 change left', 'Request Correction'):
        assert gone not in page, gone


def test_saving_the_same_value_in_setup_changes_nothing(env):
    _setup_edit(env, 'Employment Status', _current(env, 'Employment Status'))
    assert 'Employment Status was not changed.' in _flashes(env.client)
    assert _setup_edits(env) == []


def test_unused_fields_are_edited_directly_on_step5(env):
    page = env.client.get('/account-setup/verify').data.decode()
    for key in ('specialization', 'employment_type', 'employment_status'):
        assert f'toggleEdit("ed-{key}", true)' in page
    assert "toggleEdit('edName', true)" in page and "toggleEdit('edEmpId', true)" in page
    assert 'Request Correction' not in page


def test_invalid_values_and_fields_are_rejected(env):
    before = {f: _current(env, f) for f in env.app._ONE_TIME_FIELDS}
    for field, value in [('Employment Status', 'Contractual'), ('Specialization', 'No Such Spec ZZ'),
                         ('Employment Type', 'No Such Type ZZ'), ('Employment Status', ''),
                         ('faculty.isactive', 'x')]:
        _setup_edit(env, field, value)
        assert _flashes(env.client), (field, value)
    assert {f: _current(env, f) for f in env.app._ONE_TIME_FIELDS} == before
    assert _setup_edits(env) == []


def test_admin_approved_correction_updates_the_record(env):
    _finish_setup(env)
    old, new = _current(env, 'Employment Status'), _other_value(env, 'Employment Status')
    r = env.client.post('/api/user/correction-requests', json={
        'items': [{'field_name': 'Employment Status', 'proposed_value': new}], 'remarks': 'HR changed it'})
    assert r.status_code == 200
    assert _current(env, 'Employment Status') == old            # unchanged until approved
    assert env.client.get('/api/user/me').get_json()['pending_fields'] == ['Employment Status']
    rid = env.q("SELECT requestid FROM personal_info_correction_request WHERE username = %s "
                "AND field_name = 'Employment Status' AND status = 'Pending'", (env.username,))[0][0]
    admin, admin_user = env.admin_client()
    admin.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Approved'})
    assert _current(env, 'Employment Status') == new
    assert env.q("""SELECT old_value, new_value, changed_by, requestid FROM faculty_field_change_log
                    WHERE employeenumber = %s AND change_source = 'admin_correction'""",
                 (env.emp,)) == [(old, new, admin_user, rid)]
    statuses = [r['status'] for r in env.client.get('/api/user/correction-requests').get_json()['requests']]
    assert statuses == ['Approved']


def test_admin_can_approve_employment_type_correction(env):
    new = _other_value(env, 'Employment Type')
    env.client.post('/api/user/correction-requests', json={
        'items': [{'field_name': 'Employment Type', 'proposed_value': new}], 'remarks': 'Wrong type'})
    rid = env.q("SELECT requestid FROM personal_info_correction_request WHERE username = %s "
                "AND field_name = 'Employment Type' AND status = 'Pending'", (env.username,))[0][0]
    admin, _ = env.admin_client()
    admin.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Approved'})
    assert _current(env, 'Employment Type') == new


def test_temporary_password_accounts_cannot_edit(env):
    with env.client.session_transaction() as s:
        s['must_change_password'] = True
    assert env.client.post('/api/user/profile/name', data={'First Name': 'X', 'Last Name': 'Y'}).status_code == 403
    assert env.client.post('/api/user/contact/request-code', data={'contact_number': '09123456789'}).status_code == 403
    assert env.client.post('/api/user/contact/update',
                           data={'contact_number': '09123456789', 'code': '123456'}).status_code == 403


def test_pending_correction_holds_the_field_in_setup(env):
    new = _other_value(env, 'Employment Type')
    env.client.post('/api/user/correction-requests', json={
        'items': [{'field_name': 'Employment Type', 'proposed_value': new}], 'remarks': 'r'})
    _setup_edit(env, 'Employment Type', new)
    assert any('pending correction request' in m for m in _flashes(env.client))
    assert _current(env, 'Employment Type') == 'Regular' and _setup_edits(env) == []


def test_startup_creates_the_change_log(env):
    # Its absence was why every Step 5 field showed READ-ONLY.
    assert env.q("SELECT to_regclass('public.faculty_field_change_log') IS NOT NULL")[0][0] is True
    assert env.app._self_edit_ready() is True


# ---- name and contact number: editable any time -----------------------------------

def _name_form(env, **overrides):
    cur = env.q("SELECT firstname, middlename, lastname, suffix FROM faculty WHERE employeenumber = %s",
                (env.emp,))[0]
    form = {'First Name': cur[0], 'Middle Name': cur[1] or '', 'Last Name': cur[2], 'Suffix': cur[3] or ''}
    form.update(overrides)
    return form


def test_name_can_be_changed_repeatedly_and_is_logged(env):
    _finish_setup(env)                       # Profile Settings (direct edits wait while setup runs)
    for first in ('Testfirst', "Ma. Anne-Marie O'Neil"):
        r = env.client.post('/api/user/profile/name', data=_name_form(env, **{'First Name': first}))
        assert r.status_code == 200 and r.get_json()['changed'] is True
        assert _current(env, 'First Name') == first
    assert [row[2] for row in _self_edits(env, 'First Name')] == ['Testfirst', "Ma. Anne-Marie O'Neil"]


def test_a_setup_name_edit_is_saved_by_save_and_confirm(env):
    before = env.q("SELECT lastname FROM faculty WHERE employeenumber = %s", (env.emp,))[0][0]
    r = env.client.post('/account-setup/verify', data=dict(_name_form(env, **{'Last Name': 'Setuplast'}),
                                                             action='edit_name'))
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert env.q("SELECT lastname FROM faculty WHERE employeenumber = %s", (env.emp,))[0][0] == before
    assert 'Setuplast' in env.client.get('/account-setup/verify').data.decode()
    _confirm(env)
    assert env.q("SELECT lastname FROM faculty WHERE employeenumber = %s", (env.emp,))[0][0] == 'Setuplast'
    assert ('Last Name', before or '', 'Setuplast', env.username) in _setup_edits(env, 'Last Name')


def test_name_validation(env):
    before = _current(env, 'First Name')
    for bad in ({'First Name': '  '}, {'Last Name': ''}, {'First Name': 'J0hn'}, {'First Name': 'x' * 61},
                {'Suffix': '<b>'}):
        env.client.post('/account-setup/verify', data=dict(_name_form(env, **bad), action='edit_name'))
        assert any(m for m in _flashes(env.client)), bad
    _finish_setup(env)
    for bad in ({'First Name': '  '}, {'First Name': 'J0hn'}):
        r = env.client.post('/api/user/profile/name', data=_name_form(env, **bad))
        assert r.status_code == 400, bad
    assert _current(env, 'First Name') == before


def test_contact_number_has_no_unverified_save_in_profile_settings(env):
    # The old direct save is gone: a contact number change needs the emailed code
    # (tests/test_contact_change_otp.py covers that flow).
    _finish_setup(env)
    before = env.q("SELECT contactnumber FROM accounts WHERE username = %s", (env.username,))[0][0]
    r = env.client.post('/api/user/profile/contact', data={'contact_number': '+63 917 555 0199'})
    assert r.status_code in (404, 405)
    r = env.client.post('/api/user/contact/update', data={'contact_number': '+63 917 555 0199', 'code': ''})
    assert r.status_code == 400
    assert env.q("SELECT contactnumber FROM accounts WHERE username = %s", (env.username,))[0][0] == before


# ---- Designation: only for the Designee employment type ------------------------------

def _designation(env):
    return env.q("""SELECT d.designationname FROM faculty f
                    LEFT JOIN designation d ON d.designationid = f.designationid
                    WHERE f.employeenumber = %s""", (env.emp,))[0][0] or ''


def _designations(env):
    names = [r[0] for r in env.q("SELECT designationname FROM designation ORDER BY designationname")]
    if len(names) < 2:
        pytest.skip("need two designations")
    return names


def _make_designee(env, designation):
    env.q("""UPDATE faculty SET employeetypeid = (SELECT employeetypeid FROM employeetype WHERE typename = 'Designee'),
                    designationid = (SELECT designationid FROM designation WHERE designationname = %s)
             WHERE employeenumber = %s RETURNING 1""", (designation, env.emp))


def _setup_designation(env, value):
    return env.client.post('/account-setup/verify', data={'action': 'edit_designation', 'value': value})


def _setup_type(env, value, **extra):
    return env.client.post('/account-setup/verify', data=dict(
        action='edit_employment', field='Employment Type', value=value, **extra))


def test_designation_is_unavailable_for_other_employment_types(env):
    page = env.client.get('/account-setup/verify').data.decode()       # Regular (see the fixture)
    assert 'id="row-designation"' not in page and 'id="ed-designation"' not in page
    _setup_designation(env, _designations(env)[0])
    assert any('Only a Designee has a Designation' in m for m in _flashes(env.client))
    assert _designation(env) == '' and _setup_edits(env, 'Designation') == []


def test_designee_can_edit_designation_during_setup_and_it_is_logged(env):
    first, second = _designations(env)[:2]
    _make_designee(env, first)
    page = env.client.get('/account-setup/verify').data.decode()
    assert 'id="row-designation"' in page and 'toggleEdit("ed-designation", true)' in page
    r = _setup_designation(env, second)
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert _designation(env) == first and second in env.client.get('/account-setup/verify').data.decode()
    # No Designation is allowed for a Designee (as in Add Faculty); unknown values are refused.
    _setup_designation(env, 'Made Up Title XYZ')
    assert 'Please choose a Designation from the list.' in _flashes(env.client)
    _setup_designation(env, '')
    assert _designation(env) == first                                  # still nothing saved
    _confirm(env)
    assert _designation(env) == ''
    assert env.q("SELECT old_value, new_value, change_source FROM faculty_field_change_log "
                 "WHERE employeenumber = %s AND field_name = 'Designation'", (env.emp,)) == [
        (first, '', 'setup_edit')]


def test_changing_type_to_designee_saves_the_chosen_designation(env):
    name = _designations(env)[0]
    _setup_type(env, 'Designee', designation='Made Up Title XYZ')
    assert 'Please choose a Designation from the list.' in _flashes(env.client)
    _setup_type(env, 'Designee', designation=name)
    assert _current(env, 'Employment Type') == 'Regular'                # kept, not saved
    page = env.client.get('/account-setup/verify').data.decode()
    assert 'toggleEdit("ed-designation", true)' in page and name in page   # Designation available at once
    _confirm(env)
    assert _current(env, 'Employment Type') == 'Designee' and _designation(env) == name
    assert [r[0] for r in _setup_edits(env)] == ['Employment Type', 'Designation']


def test_changing_type_away_from_designee_clears_the_designation_in_setup(env):
    name = _designations(env)[0]
    _make_designee(env, name)
    _setup_type(env, 'Part-time')                                   # no confirmation needed in setup
    assert any('Your Designation was removed' in m for m in _flashes(env.client))
    assert 'id="row-designation"' not in env.client.get('/account-setup/verify').data.decode()
    assert _current(env, 'Employment Type') == 'Designee' and _designation(env) == name
    _confirm(env)
    assert _current(env, 'Employment Type') == 'Part-time' and _designation(env) == ''


def test_a_leftover_designation_on_a_non_designee_can_be_removed_and_blocks_confirm(env):
    name = _designations(env)[0]
    env.q("UPDATE faculty SET designationid = (SELECT designationid FROM designation WHERE designationname = %s) "
          "WHERE employeenumber = %s RETURNING 1", (name, env.emp))
    page = env.client.get('/account-setup/verify').data.decode()
    assert 'id="row-designation"' in page and 'Only a Designee has a Designation' in page
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert any('Your setup was not completed' in m for m in _flashes(env.client))
    _setup_designation(env, name)                                     # can't SET one as non-Designee
    assert any('Only a Designee has a Designation' in m for m in _flashes(env.client))
    _setup_designation(env, '')                                       # ...but can remove it
    _confirm(env)
    assert _designation(env) == ''


def test_profile_settings_changes_designation_only_through_a_correction(env):
    name = _designations(env)[0]
    _finish_setup(env)
    r = env.client.post('/api/user/profile/employment',
                        data={'field': 'Employment Type', 'value': 'Designee', 'designation': name})
    assert r.status_code in (404, 405)
    assert _current(env, 'Employment Type') == 'Regular' and _designation(env) == ''
    r = env.client.post('/api/user/correction-requests', json={
        'items': [{'field_name': 'Employment Type', 'proposed_value': 'Designee'}], 'remarks': 'Now a designee'})
    assert r.status_code == 200


def test_step5_choices_match_add_faculty(env):
    page = env.client.get('/account-setup/verify').data.decode()
    form = page.split('id="ed-employment_type"')[1].split('</form>')[0]
    types = [r[0] for r in env.q("SELECT typename FROM employeetype ORDER BY typename")]
    assert re.findall(r'<option value="([^"]*)"', form.split('name="designation"')[0]) == types
    status = page.split('id="ed-employment_status"')[1].split('</form>')[0]
    assert re.findall(r'<option value="([^"]*)"', status) == ['Permanent', 'Part-Time', 'Temporary']


def test_designation_is_not_directly_editable_after_setup(env):
    first, second = _designations(env)[:2]
    _make_designee(env, first)
    _finish_setup(env)
    r = _setup_designation(env, second)
    assert r.status_code == 302 and '/account-setup/' not in r.headers['Location']
    assert _designation(env) == first and _setup_edits(env, 'Designation') == []


def test_pending_designation_correction_blocks_the_direct_edit(env):
    first, second = _designations(env)[:2]
    _make_designee(env, first)
    env.client.post('/api/user/correction-requests', json={
        'items': [{'field_name': 'Designation', 'proposed_value': second}], 'remarks': 'r'})
    page = env.client.get('/account-setup/verify').data.decode()
    assert 'id="ed-designation"' not in page and 'waiting for the Administrator' in page
    _setup_designation(env, second)
    assert any('pending correction request for: Designation' in m for m in _flashes(env.client))
    assert _setup_edits(env, 'Designation') == []


def test_admin_cannot_approve_a_designation_for_a_non_designee(env):
    name = _designations(env)[0]
    env.client.post('/api/user/correction-requests', json={
        'items': [{'field_name': 'Designation', 'proposed_value': name}], 'remarks': 'r'})
    rid = env.q("SELECT requestid FROM personal_info_correction_request WHERE username = %s "
                "AND field_name = 'Designation' AND status = 'Pending'", (env.username,))[0][0]
    admin, _ = env.admin_client()
    admin.post(f'/admin/account-corrections/{rid}/decide', data={'decision': 'Approved'})
    assert env.q("SELECT status FROM personal_info_correction_request WHERE requestid = %s", (rid,))[0][0] == 'Pending'
    assert _designation(env) == ''


# ---- Step 5 layout --------------------------------------------------------------------

def test_step5_restores_the_photo_beside_email_and_contact_layout(env):
    page = env.client.get('/account-setup/verify').data.decode()
    personal = page.split('id="accTitle"')[1].split('id="offTitle"')[0]
    employment = page.split('id="offTitle"')[1].split('id="confirmForm"')[0]
    # Name first, then the photo column beside email + contact number; Employee ID heads
    # Employment Information.
    assert personal.index('Full Name') < personal.index('class="acc-grid"') and '>Employee ID</div>' not in personal
    assert employment.index('>Employee ID</div>') < employment.index('>Specialization</div>')
    # Only the information sections scroll; the confirmation sits on the right, in red.
    scroll = page.split('id="rvScroll"')[1].split('id="confirmForm"')[0]
    assert 'id="accTitle"' in scroll and 'id="offTitle"' in scroll
    assert 'justify-content: flex-end' in page.split('.rv-confirm {')[1].split('}')[0]
    assert 'color: #b22222' in page.split('.rv-confirm {')[1].split('}')[0]
    grid = personal.split('class="acc-grid"')[1]
    assert grid.index('class="acc-photo"') < grid.index('class="acc-rows"')
    assert ('Add Photo' in grid or 'Change Photo' in grid) and grid.index('acc-avatar') < grid.index('Photo</a>')
    assert 'Email Address' in grid and 'Contact Number' in grid
    assert 'SAVE AND CONFIRM' in page and 'Request Correction' not in page and 'Read-only' not in page


def test_confirm_refuses_an_incomplete_record(env):
    env.q("UPDATE faculty SET employeestatus = 'Permanent' WHERE employeenumber = %s RETURNING 1", (env.emp,))
    status = env.app._setup_record_problems
    assert status({'firstname': 'A', 'lastname': 'B', 'employeenumber': 'X1', 'specializationname': 'S',
                   'typename': 'Regular', 'employeestatus': 'Permanent'}) == []
    assert status({'firstname': ' ', 'lastname': 'B', 'employeenumber': 'bad id', 'specializationname': None,
                   'typename': 'Regular', 'employeestatus': 'Contract'}) != []


def test_step5_type_form_script_handles_the_designation(env, tmp_path):
    """The real Step 5 script, run in Node by tests/js/setup_review_harness.js."""
    import re
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    html = env.client.get('/account-setup/verify').data.decode()
    script = next(s for s in re.findall(r'<script>(.*?)</script>', html, re.S) if 'function syncTypeForm' in s)
    path = tmp_path / 'setup_review_page.js'
    path.write_text(script, encoding='utf-8')
    harness = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'js', 'setup_review_harness.js')
    out = subprocess.run([node, harness, str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr or out.stdout
    assert 'all checks passed' in out.stdout
