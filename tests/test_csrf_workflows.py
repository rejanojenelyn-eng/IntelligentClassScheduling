"""
C3 verification against the REAL views and the local database: archive, restore, delete
and account management with a valid CSRF token, and with GET / a missing token.

Every query runs on ONE connection inside a transaction that is rolled back at the end
(same pattern as test_account_setup_review), so nothing is ever kept. Rooms, buildings
and curricula that get deleted are throwaway rows created inside that transaction.
"""
import psycopg2
import pytest

from conftest import requires_db

pytestmark = requires_db
PAGE = {'Sec-Fetch-Mode': 'navigate'}


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

    def q(sql, args=()):
        c = real.cursor(); c.execute(sql, args)
        r = c.fetchall() if c.description else []
        c.close(); return r

    def client(role, token=True):
        rows = q("SELECT username FROM accounts WHERE role = %s AND isactive ORDER BY username LIMIT 1", (role,))
        if not rows:
            pytest.skip(f"no active {role} account")
        c = app_module.app.test_client()
        c.csrf_auto = token
        with c.session_transaction() as s:
            s.update(loggedin=True, username=rows[0][0], role=role,
                     must_change_password=False, account_setup_complete=True)
        return c

    def archivable_employee():
        cur = real.cursor()
        cur.execute("""SELECT f.employeenumber FROM faculty f JOIN accounts a ON a.employeenumber = f.employeenumber
                       WHERE a.role = 'Faculty' AND a.username = f.employeenumber ORDER BY f.employeenumber""")
        for (emp,) in cur.fetchall():
            if not app_module._archive_block_reasons(cur, [emp]):
                cur.close(); return emp
        cur.close()
        pytest.skip("no faculty member that can be archived")

    yield type('Env', (), dict(q=staticmethod(q), client=staticmethod(client), app=app_module,
                               archivable_employee=staticmethod(archivable_employee)))
    real.rollback()
    real.close()


def _in_faculty(env, emp):
    return bool(env.q("SELECT 1 FROM faculty WHERE employeenumber = %s", (emp,)))


def _archive_id(env, emp):
    rows = env.q("SELECT facultyarchiveid FROM faculty_archive WHERE employeenumber = %s "
                 "ORDER BY facultyarchiveid DESC LIMIT 1", (emp,))
    return rows[0][0] if rows else None


def _active(env, username):
    return env.q("SELECT isactive FROM accounts WHERE username = %s", (username,))[0][0]


@pytest.mark.parametrize('role,archive_url,restore_url,home', [
    ('Admin', '/admin/archive_employee/{}', '/admin/restore_employee/{}', '/admin/employee'),
    ('Academic Head', '/archive_employee/{}', '/restore_employee/{}', '/employee'),
])
def test_archive_and_restore_work_only_as_post_with_a_token(env, role, archive_url, restore_url, home):
    # KNOWN, SEPARATE BUG (not CSRF): some databases have a hand-added check constraint
    # chk_account_employee (non-Admin accounts must keep an employee number), while
    # archiving clears accounts.employeenumber -- so every archive fails there. Lifted
    # here, inside this rolled-back transaction only, to exercise the archive/restore path.
    env.q("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS chk_account_employee")
    emp = env.archivable_employee()
    with_token, without_token = env.client(role), env.client(role, token=False)

    # GET (a link / <img src>) and a POST without the token change nothing
    assert with_token.get(archive_url.format(emp), headers=PAGE).status_code == 405
    assert without_token.post(archive_url.format(emp), headers=PAGE).status_code == 400
    assert _in_faculty(env, emp) and _archive_id(env, emp) is None

    # the page's button: POST + token -> really archived, account deactivated
    r = with_token.post(archive_url.format(emp), headers=PAGE)
    assert r.status_code == 302 and r.headers['Location'].endswith(home)
    assert not _in_faculty(env, emp) and _archive_id(env, emp) is not None
    assert _active(env, emp) is False

    archive_id = _archive_id(env, emp)
    assert with_token.get(restore_url.format(archive_id), headers=PAGE).status_code == 405
    assert without_token.post(restore_url.format(archive_id), headers=PAGE).status_code == 400
    assert not _in_faculty(env, emp)

    r = with_token.post(restore_url.format(archive_id), headers=PAGE)
    assert r.status_code == 302
    assert _in_faculty(env, emp) and _archive_id(env, emp) is None and _active(env, emp) is True


def test_faculty_cannot_archive_or_restore_even_with_a_token(env):
    emp = env.archivable_employee()
    faculty = env.client('Faculty')
    for url in (f'/archive_employee/{emp}', f'/admin/archive_employee/{emp}',
                '/restore_employee/1', '/admin/restore_employee/1'):
        assert faculty.post(url, headers=PAGE).status_code == 403, url
    assert _in_faculty(env, emp)


def test_admin_account_management_needs_the_token(env):
    emp = env.archivable_employee()
    user_id = env.q("SELECT userid FROM accounts WHERE username = %s", (emp,))[0][0]
    admin, admin_no_token = env.client('Admin'), env.client('Admin', token=False)

    form = {'user_id': user_id, 'new_status': 'false'}
    assert admin_no_token.post('/admin/accounts/toggle_status', data=form, headers=PAGE).status_code == 400
    assert _active(env, emp) is True
    assert admin.post('/admin/accounts/toggle_status', data=form, headers=PAGE).status_code == 302
    assert _active(env, emp) is False

    env.q("UPDATE accounts SET must_change_password = FALSE WHERE userid = %s", (user_id,))
    form = {'user_id': user_id, 'username': emp}
    assert admin_no_token.post('/admin/accounts/reset_password', data=form, headers=PAGE).status_code == 400
    assert env.q("SELECT must_change_password FROM accounts WHERE userid = %s", (user_id,))[0][0] is False
    assert admin.post('/admin/accounts/reset_password', data=form, headers=PAGE).status_code == 302
    assert env.q("SELECT must_change_password FROM accounts WHERE userid = %s", (user_id,))[0][0] is True


def test_delete_room_building_and_curriculum_need_post_and_the_token(env):
    bldg = env.q("INSERT INTO building (buildingname) VALUES ('ZZ CSRF Test Bldg') RETURNING buildingid")[0][0]
    room = env.q("""INSERT INTO room (roomname, roomtype, roomcapacity, buildingid)
                    VALUES ('ZZ-CSRF-1', 'Lecture', 40, %s) RETURNING roomid""", (bldg,))[0][0]
    room2 = env.q("""INSERT INTO room (roomname, roomtype, roomcapacity, buildingid)
                     VALUES ('ZZ-CSRF-2', 'Lecture', 40, %s) RETURNING roomid""", (bldg,))[0][0]
    prog = env.q("SELECT programcode FROM curriculum LIMIT 1")[0][0]
    curr = env.q("""INSERT INTO curriculum (curriculumcode, programcode, curriculumyear)
                    VALUES ('ZZCSRF', %s, 1999) RETURNING curriculumid""", (prog,))[0][0]
    admin, admin_no_token = env.client('Admin'), env.client('Admin', token=False)
    exists = {'room': lambda i: bool(env.q("SELECT 1 FROM room WHERE roomid = %s", (i,))),
              'building': lambda i: bool(env.q("SELECT 1 FROM building WHERE buildingid = %s", (i,))),
              'curriculum': lambda i: bool(env.q("SELECT 1 FROM curriculum WHERE curriculumid = %s", (i,)))}

    for kind, url, rid in [('room', f'/admin/delete_room/{room}', room),
                           ('curriculum', f'/admin/curriculum/delete/{curr}', curr),
                           ('building', f'/admin/delete_building/{bldg}', bldg)]:
        assert admin.get(url, headers=PAGE).status_code == 405, url
        assert admin_no_token.post(url, headers=PAGE).status_code == 400, url
        assert exists[kind](rid), url
        r = admin.post(url, headers=dict(PAGE, Referer='http://localhost/admin/curriculum'))
        assert r.status_code == 302, (url, r.status_code)
        assert not exists[kind](rid), url
    assert not exists['room'](room2)                    # the building took its other room with it
