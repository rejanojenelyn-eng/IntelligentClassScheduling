"""Account Setup Step 5: a faculty member may change their OWN Employee ID directly.

employeenumber is faculty's primary key, so the change must move every reference (accounts,
schedules, requests, logs...) and never detach the account or take over someone else's ID.
Runs the real Flask routes against the local database inside one rolled-back transaction.
"""
import os

import pytest
from psycopg2.extras import RealDictCursor

from tests.conftest import requires_db, DB_AVAILABLE
from tests._shared_tx import SharedTx, install

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module

NEW_ID = 'TESTEMP-0001'
_MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'migrations', '2026-10-22_one_time_employment_edit.sql')


@pytest.fixture
def env(monkeypatch):
    t = SharedTx()
    install(monkeypatch, t)
    conn = t.handle()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    with open(_MIGRATION, encoding='utf-8') as f:      # applied inside the rolled-back transaction
        cur.execute(f.read())
    # The faculty account with the most schedule rows (and no immutable archived Local history).
    cur.execute("""
        SELECT a.username, a.userid, a.role, f.employeenumber,
               (SELECT COUNT(*) FROM schedule s WHERE s.employeenumber = f.employeenumber) AS n
        FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.isactive AND a.role IN ('Faculty', 'Academic Head')
          AND NOT EXISTS (SELECT 1 FROM local_arrangement_sessions ls
                          JOIN local_arrangement la ON la.arrangementid = ls.arrangementid
                          WHERE ls.faculty_employeenumber = f.employeenumber AND la.status = 'Archived')
        ORDER BY n DESC, a.username LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        t.close()
        pytest.skip('no faculty account to test with')
    cur.execute("UPDATE accounts SET account_setup_complete = FALSE, must_change_password = FALSE "
                "WHERE username = %s", [row['username']])
    cur.execute("DELETE FROM personal_info_correction_request WHERE username = %s", [row['username']])
    cur.execute("DELETE FROM account_setup_draft WHERE username = %s", [row['username']])
    conn.commit()
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, username=row['username'], role=row['role'],
                 must_change_password=False, account_setup_complete=False)

    def q(sql, args=()):
        c = conn.cursor(); c.execute(sql, args)
        r = c.fetchall() if c.description else []
        c.close(); return r

    try:
        yield type('Env', (), dict(client=client, q=staticmethod(q), conn=conn, **row))
    finally:
        t.close()


def _reference_columns(env):
    cols = {(t, c) for t, c in env.q("""
        SELECT c.conrelid::regclass::text, a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'f' AND c.confrelid = 'public.faculty'::regclass""")}
    return sorted(cols | set(app_module._EMPLOYEE_ID_TEXT_COLUMNS))


def _counts(env, emp):
    return {(t, c): env.q(f'SELECT COUNT(*) FROM {t} WHERE "{c}" = %s', (emp,))[0][0]
            for t, c in _reference_columns(env)}


def _edit(env, value):
    return env.client.post('/account-setup/verify', data={'action': 'edit_employee_id', 'value': value})


def _flashes(env):
    with env.client.session_transaction() as s:
        return [m for _, m in s.get('_flashes', [])]


def _faculty_ids(env):
    return {r[0] for r in env.q("SELECT employeenumber FROM faculty")}


def test_employee_id_change_moves_every_reference_and_keeps_the_account(env):
    old = env.employeenumber
    names = env.q("SELECT firstname, lastname, specializationid, employeetypeid FROM faculty "
                  "WHERE employeenumber = %s", (old,))[0]
    # Earlier change-log history must follow the rename.
    env.q("""INSERT INTO faculty_field_change_log (employeenumber, field_name, old_value, new_value,
                                                   change_source, changed_by)
             VALUES (%s, 'Specialization', 'a', 'b', 'self_edit', %s)""", (old, env.username))
    before = _counts(env, old)
    assert before[('accounts', 'employeenumber')] == 1

    r = _edit(env, NEW_ID)
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert f'Employee ID updated to {NEW_ID}.' in _flashes(env)
    # Kept with the setup: nothing is renamed until Save and Confirm.
    assert _counts(env, old) == before and NEW_ID not in _faculty_ids(env)
    assert NEW_ID in env.client.get('/account-setup/verify').data.decode()
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/done'), _flashes(env)

    expected = dict(before)
    expected[('faculty_field_change_log', 'employeenumber')] += 1      # + the "Employee ID changed" row
    print('references moved:', {k: v for k, v in before.items() if v})
    assert _counts(env, NEW_ID) == expected                  # every reference moved...
    assert not any(_counts(env, old).values())               # ...and none left on the old ID
    assert old not in _faculty_ids(env)
    assert env.q("SELECT firstname, lastname, specializationid, employeetypeid FROM faculty "
                 "WHERE employeenumber = %s", (NEW_ID,))[0] == names
    # Same account (same row, same login), now linked to the new ID.
    assert env.q("SELECT userid, username, employeenumber, account_setup_complete FROM accounts "
                 "WHERE userid = %s", (env.userid,))[0] == (env.userid, env.username, NEW_ID, True)
    assert env.client.get('/api/user/me').get_json()['emp_num'] == NEW_ID
    assert ('Employee ID', old, NEW_ID) in env.q(
        "SELECT field_name, old_value, new_value FROM faculty_field_change_log "
        "WHERE employeenumber = %s AND change_source = 'setup_edit'", (NEW_ID,))
    if before[('schedule', 'employeenumber')]:
        # Scheduling records still resolve to the same faculty member.
        assert env.q("SELECT COUNT(*) FROM schedule s JOIN faculty f ON f.employeenumber = s.employeenumber "
                     "WHERE f.employeenumber = %s", (NEW_ID,))[0][0] == before[('schedule', 'employeenumber')]


def test_an_employee_id_in_use_is_refused(env):
    other = env.q("SELECT employeenumber FROM faculty WHERE employeenumber <> %s ORDER BY 1 LIMIT 1",
                  (env.employeenumber,))[0][0]
    other_user = env.q("SELECT username FROM accounts WHERE userid <> %s ORDER BY userid LIMIT 1",
                       (env.userid,))[0][0]
    archived = env.q("SELECT employeenumber FROM faculty_archive WHERE employeenumber IS NOT NULL LIMIT 1")
    taken = [other, other.lower() if other.lower() != other else other.upper(), other_user]
    taken += [archived[0][0]] if archived else []
    before = _counts(env, env.employeenumber)
    for value in taken:
        if not app_module._EMPLOYEE_ID_RE.fullmatch(value):
            continue
        _edit(env, value)
        assert any('already belongs to another faculty member or account' in m for m in _flashes(env)), value
    assert _counts(env, env.employeenumber) == before
    assert env.q("SELECT employeenumber FROM accounts WHERE userid = %s", (env.userid,))[0][0] == env.employeenumber


def test_invalid_employee_ids_are_refused(env):
    for bad in ('', '   ', 'has space', 'x' * 31, '-12345', "1'; DROP TABLE faculty;--", 'ÉMP1'):
        _edit(env, bad)
        assert any('Please enter a valid Employee ID' in m for m in _flashes(env)), bad
    assert env.employeenumber in _faculty_ids(env)


def test_same_id_is_unchanged_and_setup_is_not_finished(env):
    _edit(env, env.employeenumber)
    assert 'Employee ID was not changed.' in _flashes(env)
    assert env.q("SELECT account_setup_complete FROM accounts WHERE userid = %s", (env.userid,))[0][0] is False


def test_employee_id_is_not_directly_editable_after_setup(env):
    env.q("UPDATE accounts SET account_setup_complete = TRUE WHERE userid = %s", (env.userid,))
    with env.client.session_transaction() as s:
        s['account_setup_complete'] = True
    r = _edit(env, NEW_ID)
    assert r.status_code == 302 and '/account-setup/' not in r.headers['Location']
    assert NEW_ID not in _faculty_ids(env)
    assert env.q("SELECT 1 FROM account_setup_draft WHERE userid = %s", (env.userid,)) == []


def test_an_id_taken_before_save_and_confirm_saves_nothing(env):
    _edit(env, NEW_ID)
    # Someone else gets that ID before the user confirms.
    env.q("INSERT INTO faculty_archive (employeenumber, firstname, lastname) VALUES (%s, 'X', 'Y')", (NEW_ID,))
    r = env.client.post('/account-setup/verify', data={'action': 'confirm'})
    assert r.headers['Location'].endswith('/account-setup/verify')
    assert any(f'The Employee ID {NEW_ID} is now used by another faculty member or account' in m
               for m in _flashes(env))
    assert env.employeenumber in _faculty_ids(env)
    assert env.q("SELECT account_setup_complete FROM accounts WHERE userid = %s", (env.userid,))[0][0] is False


def test_pending_employee_id_correction_blocks_the_direct_edit(env):
    env.q("""INSERT INTO personal_info_correction_request
                 (username, employeenumber, field_name, current_value, proposed_value, remarks, status)
             VALUES (%s, %s, 'Employee ID', %s, 'X1', 'r', 'Pending')""",
          (env.username, env.employeenumber, env.employeenumber))
    page = env.client.get('/account-setup/verify').data.decode()
    assert "toggleEdit('edEmpId', true)" not in page and 'waiting for the Administrator' in page
    _edit(env, NEW_ID)
    assert any('pending correction request for: Employee ID' in m for m in _flashes(env))
    assert NEW_ID not in _faculty_ids(env)
