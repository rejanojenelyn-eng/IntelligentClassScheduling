"""Bulk / curriculum deletes never orphan request or Local Scheduler history.

DB-backed tests run inside one rolled-back transaction (tests/_shared_tx.py).
"""
import datetime
import inspect

import pytest

import app as app_module
from conftest import requires_db
from test_local_override_snapshot_repair import tx, ls, client, _cur  # noqa: F401  (fixtures)
from test_official_persistence_repair import scope, _row, _seed  # noqa: F401  (fixtures)


def _version_count(tx, ls):
    cur = _cur(tx)
    cur.execute("""SELECT COUNT(*) AS n FROM schedule_version sv JOIN schedule s ON sv.scheduleid = s.scheduleid
                   WHERE s.sectionid = %s AND s.semesterid = %s""", (ls['sectionid'], ls['semesterid']))
    return cur.fetchone()['n']


@pytest.fixture
def head(ls):
    """An Academic Head session with the fields the session guard checks."""
    import uuid
    c = app_module.app.test_client()
    now = datetime.datetime.now().timestamp()
    with c.session_transaction() as sess:
        sess.update(loggedin=True, role='Academic Head', username=ls['username'],
                    employeenumber=ls['empnum'], last_seen=now, last_active=now, sid=uuid.uuid4().hex)
    return c


def _list_delete(client, ls):
    return client.post('/api/schedule/list/delete', json={
        'programcode': ls['program'], 'yearlevel': ls['yearlevel'],
        'academicyearid': ls['academicyearid'], 'semestertype': ls['semestertype']})


@requires_db
def test_list_delete_removes_only_the_current_schedule_and_keeps_history(tx, ls, head):
    """Schedule List → Delete archives the entry's Draft/Published versions; nothing is
    hard-deleted, so the Version History and any Local history that points at it stay."""
    o = ls['occ'][0]
    cur = _cur(tx)
    cur.execute("""INSERT INTO local_arrangement (programcode, yearlevel, sectionid, semesterid,
                       ref_versionid, is_active, status) VALUES (%s,%s,%s,%s,%s,FALSE,'Archived')
                   RETURNING arrangementid""",
                (ls['program'], ls['yearlevel'], ls['sectionid'], ls['semesterid'], o['versionid']))
    arr = cur.fetchone()['arrangementid']
    cur.execute("""INSERT INTO local_arrangement_sessions (arrangementid, subjectcode, daydesc, starttimeid,
                       endtimeid, roomid, official_sessionid) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (arr, o['code'], o['daydesc'], o['starttimeid'], o['endtimeid'], o['roomid'], o['sessionid']))
    tx.handle().commit()
    before = _version_count(tx, ls)
    r = _list_delete(head, ls)
    body = r.get_json()
    assert r.status_code == 200 and body['success'], body
    assert _version_count(tx, ls) == before                       # history kept
    cur.execute("""SELECT COUNT(*) AS n FROM schedule_version sv JOIN schedule s ON sv.scheduleid = s.scheduleid
                   WHERE s.sectionid = %s AND s.semesterid = %s AND sv.status IN ('Draft', 'Published')""",
                (ls['sectionid'], ls['semesterid']))
    assert cur.fetchone()['n'] == 0                               # nothing current left
    cur.execute("SELECT COUNT(*) AS n FROM local_arrangement_sessions WHERE arrangementid = %s", (arr,))
    assert cur.fetchone()['n'] == 1                               # Local history intact
    assert _list_delete(head, ls).status_code == 404              # already removed


@requires_db
def test_list_delete_keeps_requests_that_reference_the_schedules(tx, ls, head):
    o = ls['occ'][0]
    cur = _cur(tx)
    cur.execute("""INSERT INTO class_meeting_request (scheduleid, requested_date, new_starttimeid,
                       new_endtimeid, reason, submitted_by, status)
                   VALUES (%s, %s, %s, %s, 'x', %s, 'Pending')""",
                (o['scheduleid'], datetime.date.today(), o['starttimeid'], o['endtimeid'], o['fac']))
    tx.handle().commit()
    before = _version_count(tx, ls)
    r = _list_delete(head, ls)
    assert r.status_code == 200, r.get_json()
    assert _version_count(tx, ls) == before                       # nothing hard-deleted
    cur.execute("SELECT COUNT(*) AS n FROM class_meeting_request WHERE scheduleid = %s", (o['scheduleid'],))
    assert cur.fetchone()['n'] >= 1


@requires_db
def test_unreferenced_schedules_have_no_blockers(tx, scope):
    _seed(tx, scope, 'Published', 1, [_row(scope, scope['A'], 'Monday', '07:30', '09:00')])
    cur = _cur(tx)
    cur.execute("SELECT scheduleid FROM schedule WHERE sectionid = %s AND semesterid = %s",
                (scope['sectionid'], scope['semesterid']))
    ids = [r['scheduleid'] for r in cur.fetchall()]
    assert ids and app_module._schedule_delete_blockers(cur, ids) == {}


def test_schedule_list_delete_never_hard_deletes():
    src = inspect.getsource(app_module.api_schedule_list_delete)
    assert 'DELETE FROM schedule' not in src and "SET status = 'Archive'" in src


@pytest.mark.parametrize('fn', ['sis_import_confirm', 'admin_delete_curriculum'])
def test_every_bulk_delete_path_checks_references_before_deleting(fn):
    src = inspect.getsource(getattr(app_module, fn))
    guard = src.index('_schedule_delete_blockers(')
    first_delete = min(i for i in (src.find('DELETE FROM schedule_version'),
                                   src.find('DELETE FROM schedule_sessions')) if i >= 0)
    assert guard < first_delete


def test_curriculum_delete_no_longer_deletes_request_history():
    assert 'DELETE FROM class_meeting_request' not in inspect.getsource(app_module.admin_delete_curriculum)
