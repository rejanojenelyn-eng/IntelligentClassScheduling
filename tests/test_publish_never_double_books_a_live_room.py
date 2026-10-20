"""
Publish must never put a class into a room/time another section still holds LIVE.

Another section's Published class stays live until that section publishes again — even
when a pending Draft of theirs moves it. The Publish check used to drop such a Published
row (the editor's "superseded by a Draft" rule), so a section could publish into the
same room/time and two Published classes ended up double-booked in one room.
Runs inside a rolled-back transaction; the publish itself is a dry_run.
"""
import io
import contextlib
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


@pytest.fixture
def client(tx):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Academic Head account')
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=acct['username'], last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex)
    return c


def _live_class_moved_by_a_draft(cur):
    """Section A's Published class (room R, day D, slot) + a Draft of A that moves it."""
    cur.execute("""
        SELECT ss.sessionid, ss.versionid, ss.roomid, r.roomname, ss.daydesc, ss.starttimeid, ss.endtimeid,
               s.scheduleid, s.sectionid, s.semesterid, v.version_number
        FROM schedule_sessions ss
        JOIN schedule_version v ON v.versionid = ss.versionid AND v.status = 'Published'
             AND v.source IS DISTINCT FROM 'local'
        JOIN schedule s ON s.scheduleid = v.scheduleid
        JOIN room r ON r.roomid = ss.roomid
        JOIN semester sm ON sm.semesterid = s.semesterid AND sm.isactive
        WHERE NOT EXISTS (SELECT 1 FROM schedule_version d WHERE d.scheduleid = s.scheduleid AND d.status = 'Draft')
        ORDER BY ss.sessionid LIMIT 1
    """)
    a = cur.fetchone()
    if not a:
        pytest.skip('no Published session in the active term')
    cur.execute("INSERT INTO schedule_version (scheduleid, version_number, status, source) "
                "VALUES (%s, %s, 'Draft', 'official') RETURNING versionid",
                (a['scheduleid'], a['version_number'] + 1))
    vid = cur.fetchone()['versionid']
    other_day = 'Saturday' if a['daydesc'] != 'Saturday' else 'Friday'
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, %s)",
                (vid, other_day, a['starttimeid'], a['endtimeid'], a['roomid']))
    return a


def _another_section(cur, a):
    cur.execute("""
        SELECT sec.sectionid, pyl.programcode, pyl.yearlevel, sm.academicyearid, sm.semestertype,
               cs.subjectcode, cs.subjectname, cs.lecturehours, cs.laboratoryhours, cs.creditunits
        FROM sections sec
        JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        JOIN semester sm ON sm.semesterid = %s AND pyl.academicyearid = sm.academicyearid
        JOIN curriculumsubject cs ON cs.curriculumid = pyl.curriculumid AND cs.yearlevel = pyl.yearlevel
             AND cs.semester = sm.semestertype
        WHERE sec.sectionid <> %s AND sec.isactive
          -- B has nothing of its own at that day/time (only the room clash is under test)
          AND NOT EXISTS (
              SELECT 1 FROM schedule s2
              JOIN schedule_version v2 ON v2.scheduleid = s2.scheduleid AND v2.status IN ('Draft', 'Published')
              JOIN schedule_sessions ss2 ON ss2.versionid = v2.versionid
              WHERE s2.sectionid = sec.sectionid AND s2.semesterid = sm.semesterid
                AND ss2.daydesc = %s AND ss2.starttimeid < %s AND ss2.endtimeid > %s)
        ORDER BY sec.sectionid, cs.subjectcode LIMIT 1
    """, (a['semesterid'], a['sectionid'], a['daydesc'], a['endtimeid'], a['starttimeid']))
    b = cur.fetchone()
    if not b:
        pytest.skip('no second section with a curriculum subject')
    return b


@requires_db
def test_publish_into_a_room_another_section_still_holds_is_refused(tx, client):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    a = _live_class_moved_by_a_draft(cur)
    b = _another_section(cur, a)
    cur.execute("SELECT TO_CHAR(timevalue, 'HH12:MI AM') AS t FROM timeslot WHERE timeid IN (%s, %s) ORDER BY timeid",
                (a['starttimeid'], a['endtimeid']))
    st, et = [r['t'] for r in cur.fetchall()]
    row = {'subject_code': b['subjectcode'], 'subjectcode': b['subjectcode'], 'subject_name': b['subjectname'],
           'class_type': 'Lecture', 'faculty_id': 'TBA', 'instructor': 'TBA',
           'room_id': str(a['roomid']), 'room': a['roomname'], 'day': a['daydesc'], 'days_list': [a['daydesc']],
           'start_time': st, 'end_time': et, 'units': b['creditunits'],
           'lec_hours': b['lecturehours'], 'lab_hours': b['laboratoryhours'], 'section_id': b['sectionid']}
    with contextlib.redirect_stdout(io.StringIO()):
        body = client.post('/api/schedule/approve', json={
            'schedule_data': [row], 'override': True, 'dry_run': True,
            'context': {'program': b['programcode'], 'yearLevel': b['yearlevel'], 'term': b['semestertype'],
                        'acadYear': b['academicyearid'], 'sectionId': b['sectionid']}}).get_json()
    room_clash = [v for v in (body.get('violations') or [])
                  if a['roomname'] in str(v.get('detail') or '') or 'Room' in str(v.get('type') or '')]
    assert body.get('success') is not True, body
    assert room_clash, body
