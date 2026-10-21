"""
A Local arrangement / Schedule Adjustment moves ONE Official meeting and keeps its hours:
a 2h meeting can never become a 5h one (COMP 002 BSIT 1 was approved as Monday 12-2 ->
Tuesday 8-1). Checked at the Local save and at approval — submission already did.
Runs inside a rolled-back transaction.
"""
import uuid
from datetime import datetime

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db


@pytest.fixture
def tx(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    try:
        yield t
    finally:
        t.close()


def _official_meeting(tx, hours=2.0):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT ss.sessionid, ss.versionid, ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid,
               sv.scheduleid, sc.employeenumber, sc.sectionid, cs.subjectcode
        FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid AND sv.status = 'Published'
             AND sv.source IS DISTINCT FROM 'local'
        JOIN schedule sc ON sc.scheduleid = sv.scheduleid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        WHERE (ss.endtimeid - ss.starttimeid) * 0.5 = %s AND ss.roomid IS NOT NULL
          AND sc.employeenumber IS NOT NULL AND ss.endtimeid + 6 <= 28
        ORDER BY ss.sessionid LIMIT 1
    """, [hours])
    row = cur.fetchone()
    if not row:
        pytest.skip(f'no {hours:g}h Published meeting')
    return row


@requires_db
def test_local_save_refuses_a_meeting_with_other_hours(tx):
    m = _official_meeting(tx)
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    sess = {'subjectcode': m['subjectcode'], 'daydesc': m['daydesc'], 'starttimeid': m['starttimeid'],
            'endtimeid': m['endtimeid'] + 6, 'roomid': m['roomid'], 'official_sessionid': m['sessionid']}
    ok, err = app_module._validate_local_editable_dimensions(cur, sess, m['employeenumber'], [])
    assert ok is False and 'keeps the same hours' in err
    ok2, err2 = app_module._validate_local_editable_dimensions(cur, dict(sess, endtimeid=m['endtimeid']),
                                                               m['employeenumber'], [])
    assert 'keeps the same hours' not in (err2 or '')


@requires_db
def test_approval_refuses_an_adjustment_with_other_hours(tx):
    m = _official_meeting(tx)
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        INSERT INTO schedule_change_request
            (scheduleid, versionid, official_sessionid, change_type, new_daydesc,
             new_starttimeid, new_endtimeid, new_roomid, reason, submitted_by, status, effective_from)
        VALUES (%s, %s, %s, 'Day+Time+Room', %s, %s, %s, %s, 'test', %s, 'Pending', CURRENT_DATE)
        RETURNING requestid
    """, [m['scheduleid'], m['versionid'], m['sessionid'], m['daydesc'], m['starttimeid'],
          m['endtimeid'] + 6, m['roomid'], m['employeenumber']])
    req_id = cur.fetchone()['requestid']
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' LIMIT 1")
    head = cur.fetchone()
    if not head:
        pytest.skip('no Academic Head account')
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=head['username'], last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex)
    r = c.post('/api/requests/decide', json={'type': 'adjustment', 'id': req_id, 'decision': 'Approved'})
    body = r.get_json()
    assert r.status_code == 409 and body.get('code') == 'HOURS_CHANGED', body
    cur.execute("SELECT status FROM schedule_change_request WHERE requestid = %s", [req_id])
    assert cur.fetchone()['status'] == 'Pending'
