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


def _list_delete(client, ls):
    return client.post('/api/schedule/list/delete', json={
        'programcode': ls['program'], 'yearlevel': ls['yearlevel'],
        'academicyearid': ls['academicyearid'], 'semestertype': ls['semestertype']})


@requires_db
def test_list_delete_is_refused_when_local_history_references_the_schedules(tx, ls, client):
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
    r = _list_delete(client, ls)
    body = r.get_json()
    assert r.status_code == 409 and body['code'] == 'SCHEDULE_HISTORY_REFERENCED', body
    assert body['references']['local_sessions'] >= 1 and body['references']['local_arrangements'] >= 1
    assert _version_count(tx, ls) == before


@requires_db
def test_list_delete_is_refused_when_a_request_references_the_schedules(tx, ls, client):
    o = ls['occ'][0]
    cur = _cur(tx)
    cur.execute("""INSERT INTO class_meeting_request (scheduleid, requested_date, new_starttimeid,
                       new_endtimeid, reason, submitted_by, status)
                   VALUES (%s, %s, %s, %s, 'x', %s, 'Pending')""",
                (o['scheduleid'], datetime.date.today(), o['starttimeid'], o['endtimeid'], o['fac']))
    tx.handle().commit()
    before = _version_count(tx, ls)
    r = _list_delete(client, ls)
    assert r.status_code == 409 and r.get_json()['references']['makeup_requests'] >= 1
    assert _version_count(tx, ls) == before


@requires_db
def test_unreferenced_schedules_have_no_blockers(tx, scope):
    _seed(tx, scope, 'Published', 1, [_row(scope, scope['A'], 'Monday', '07:30', '09:00')])
    cur = _cur(tx)
    cur.execute("SELECT scheduleid FROM schedule WHERE sectionid = %s AND semesterid = %s",
                (scope['sectionid'], scope['semesterid']))
    ids = [r['scheduleid'] for r in cur.fetchall()]
    assert ids and app_module._schedule_delete_blockers(cur, ids) == {}


@pytest.mark.parametrize('fn', ['api_schedule_list_delete', 'sis_import_confirm', 'admin_delete_curriculum'])
def test_every_bulk_delete_path_checks_references_before_deleting(fn):
    src = inspect.getsource(getattr(app_module, fn))
    guard = src.index('_schedule_delete_blockers(')
    first_delete = min(i for i in (src.find('DELETE FROM schedule_version'),
                                   src.find('DELETE FROM schedule_sessions')) if i >= 0)
    assert guard < first_delete


def test_curriculum_delete_no_longer_deletes_request_history():
    assert 'DELETE FROM class_meeting_request' not in inspect.getsource(app_module.admin_delete_curriculum)
