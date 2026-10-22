"""
Schedule List → FACULTY PREVIEW toggle. Before a semester starts, faculty see none of its
schedule. With the toggle on for an entry (program + year level + semester), each faculty
member sees THEIR OWN subjects of it in My Teaching Assignment; turning it off hides them
again. Runs inside a rolled-back transaction.
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


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


def _client(username, role):
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role=role, username=username, last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex)
    return c


@pytest.fixture
def upcoming(tx):
    """A Published class of a faculty member in a semester that has not started."""
    cur = _cur(tx)
    cur.execute("""
        SELECT sec.sectionid, UPPER(pyl.programcode) AS program, pyl.yearlevel,
               sem.semesterid, sem.academicyearid, sem.semestertype
        FROM sections sec
        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        JOIN semester sem ON sem.academicyearid = pyl.academicyearid
        WHERE NOT COALESCE(sem.isactive, FALSE) AND sem.semstartdate > CURRENT_DATE AND sec.isactive
          AND NOT EXISTS (SELECT 1 FROM schedule s WHERE s.sectionid = sec.sectionid AND s.semesterid = sem.semesterid)
          AND EXISTS (SELECT 1 FROM curriculumsubject cs JOIN curriculum c ON cs.curriculumid = c.curriculumid
                      WHERE c.programcode = pyl.programcode AND cs.yearlevel = pyl.yearlevel
                        AND cs.semester = sem.semestertype)
        ORDER BY sem.semstartdate, sec.sectionid LIMIT 1
    """)
    sc = cur.fetchone()
    if not sc:
        pytest.skip('no empty section in an upcoming semester')
    cur.execute("""
        SELECT UPPER(cs.subjectcode) AS code FROM curriculumsubject cs JOIN curriculum c ON cs.curriculumid = c.curriculumid
        WHERE UPPER(c.programcode) = %s AND cs.yearlevel = %s AND cs.semester = %s ORDER BY 1 LIMIT 1
    """, (sc['program'], sc['yearlevel'], sc['semestertype']))
    code = cur.fetchone()['code']
    cur.execute("""SELECT a.username, a.employeenumber FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
                   WHERE a.isactive AND a.role = 'Faculty' ORDER BY a.userid LIMIT 1""")
    fac = cur.fetchone()
    cur.execute("SELECT roomid FROM room WHERE COALESCE(isactive, TRUE) ORDER BY roomid LIMIT 1")
    room = cur.fetchone()['roomid']
    if not fac:
        pytest.skip('no Faculty account')
    row = {'subjectcode': code, 'subject_code': code, 'daydesc': 'Monday', 'day': 'Monday', 'days_list': ['Monday'],
           'start_time': '07:30', 'end_time': '09:00', 'roomid': room, 'room_id': room,
           'employeenumber': fac['employeenumber'], 'faculty_id': fac['employeenumber'], 'sem': sc['semestertype']}
    app_module._insert_batch(cur, [row], sc['semesterid'], 'Published', 1, sc['program'], sc['yearlevel'],
                             source='manual_editor', section_id=sc['sectionid'])
    tx.handle().commit()
    return dict(sc, code=code, fac=fac)


def _sees(tx, up):
    fac = _client(up['fac']['username'], 'Faculty')
    rows = fac.get('/api/get_faculty_schedule', query_string={
        'emp_num': up['fac']['employeenumber'], 'ay_id': up['academicyearid'],
        'semester': up['semestertype'], 'official_only': 1}).get_json()
    load = fac.get('/api/faculty/my_teaching_load', query_string={
        'ay_id': up['academicyearid'], 'semester': up['semestertype'], 'source': 'official'}).get_json()
    page = fac.get('/faculty_teaching_assignment').get_data(as_text=True)
    codes = {r['subjectcode'].upper() for r in rows}
    return up['code'] in codes, bool(load.get('success')) and any(
        up['code'] in str(v) for v in load.values()), f"?ay={up['academicyearid']}&sem={up['semestertype']}" in page


def _toggle(tx, up, on):
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' LIMIT 1")
    head = _client(cur.fetchone()['username'], 'Academic Head')
    r = head.post('/api/schedule/list/release', json={
        'programcode': up['program'], 'yearlevel': up['yearlevel'],
        'academicyearid': up['academicyearid'], 'semestertype': up['semestertype'], 'released': on})
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()


@requires_db
def test_hidden_until_released_then_only_in_teaching_assignment(tx, upcoming):
    assert _sees(tx, upcoming) == (False, False, False)        # not released: nothing
    _toggle(tx, upcoming, True)
    assert _sees(tx, upcoming) == (True, True, True)           # released: own subjects + preview switch
    _toggle(tx, upcoming, False)
    assert _sees(tx, upcoming) == (False, False, False)        # off again


@requires_db
def test_schedule_list_shows_the_toggle_state(tx, upcoming):
    _toggle(tx, upcoming, True)
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' LIMIT 1")
    html = _client(cur.fetchone()['username'], 'Academic Head').get('/schedule').get_data(as_text=True)
    assert '&#34;released&#34;: true' in html or '"released": true' in html or '&quot;released&quot;: true' in html


@requires_db
def test_only_the_academic_head_can_toggle(tx, upcoming):
    r = _client(upcoming['fac']['username'], 'Faculty').post('/api/schedule/list/release', json={
        'programcode': upcoming['program'], 'yearlevel': upcoming['yearlevel'],
        'academicyearid': upcoming['academicyearid'], 'semestertype': upcoming['semestertype'], 'released': True})
    assert r.status_code == 403


@requires_db
def test_turn_all_on_and_off_at_once(tx, upcoming):
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' LIMIT 1")
    head = _client(cur.fetchone()['username'], 'Academic Head')
    cur.execute("SELECT academicyearid, semestertype FROM semester WHERE isactive LIMIT 1")
    active = cur.fetchone()
    entries = [{'programcode': upcoming['program'], 'yearlevel': upcoming['yearlevel'],
                'academicyearid': upcoming['academicyearid'], 'semestertype': upcoming['semestertype']}]
    if active:   # an entry of a semester that already started is skipped, not "released"
        entries.append({'programcode': upcoming['program'], 'yearlevel': upcoming['yearlevel'],
                        'academicyearid': active['academicyearid'], 'semestertype': active['semestertype']})
    r = head.post('/api/schedule/list/release', json={'released': True, 'entries': entries}).get_json()
    assert r['success'] and r['changed'] == 1, r
    assert _sees(tx, upcoming)[0] is True
    r = head.post('/api/schedule/list/release', json={'released': False, 'entries': entries}).get_json()
    assert r['success'] and r['changed'] == 1, r
    assert _sees(tx, upcoming)[0] is False
