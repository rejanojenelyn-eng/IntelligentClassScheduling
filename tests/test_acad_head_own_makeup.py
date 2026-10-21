"""
Local Scheduler → SAVE AS MAKE UP CLASS (the Academic Head's own class): one meeting on a
chosen date, saved already Approved (no request to approve herself), after the same
validation a faculty make-up request and its approval get. Runs inside a rolled-back
transaction.
"""
import uuid
from datetime import date, datetime, timedelta

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


def _client(username, role='Academic Head', **extra):
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        # Like a real login: no employeenumber in the session.
        s.update(loggedin=True, role=role, username=username, last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex, **extra)
    return c


def _own_class(tx):
    """An Academic Head and one of her own Published classes with a free future date."""
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT a.username, a.employeenumber, cs.subjectcode, sc.sectionid, sc.scheduleid,
               ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid,
               TO_CHAR(ts_s.timevalue, 'HH12:MI AM') AS st, TO_CHAR(ts_e.timevalue, 'HH12:MI AM') AS et,
               sem.semenddate
        FROM accounts a
        JOIN schedule sc ON sc.employeenumber = a.employeenumber
        JOIN schedule_version sv ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
             AND sv.source IS DISTINCT FROM 'local'
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        JOIN timeslot ts_s ON ts_s.timeid = ss.starttimeid
        JOIN timeslot ts_e ON ts_e.timeid = ss.endtimeid
        WHERE a.isactive AND a.role = 'Academic Head' AND ss.roomid IS NOT NULL
          AND sem.semenddate > CURRENT_DATE + 14
        ORDER BY sc.scheduleid LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        pytest.skip("no Published class of an Academic Head in the active semester")
    # The class's own weekday next week: its weekly meeting is the class itself, so that slot
    # is free for a make-up only on a day without its regular meeting -> use the day after.
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    d = date.today() + timedelta(days=7)
    want = (days.index(row['daydesc']) + 1) % 7
    while d.weekday() != want:
        d += timedelta(days=1)
    row['free_date'] = d.isoformat()
    return row


def _body(c, **over):
    b = {'subject_code': c['subjectcode'], 'section_id': c['sectionid'], 'request_date': c['free_date'],
         'start_time': c['st'], 'end_time': c['et'], 'room_id': c['roomid'], 'reason': 'Typhoon suspension'}
    b.update(over)
    return b


def _status(tx, req_id):
    cur = tx.raw.cursor()
    cur.execute("SELECT status, decided_by FROM class_meeting_request WHERE requestid = %s", [req_id])
    return cur.fetchone()


@requires_db
def test_own_make_up_is_saved_approved_and_listed(tx):
    c = _own_class(tx)
    head = _client(c['username'])
    r = head.post('/api/manual/own_makeup', json=_body(c))
    body = r.get_json()
    if r.status_code == 409:
        pytest.skip(f"that date is taken in the current data: {body.get('error')}")
    assert r.status_code == 200 and body['success'], body
    assert _status(tx, body['requestid']) == ('Approved', str(c['employeenumber']))
    assert [m['date'] for m in body['makeups']] == [c['free_date']]
    listed = head.get('/api/manual/own_makeups', query_string={
        'subject_code': c['subjectcode'], 'section_id': c['sectionid']}).get_json()
    assert listed['mine'] is True and listed['makeups'][0]['requestid'] == body['requestid']

    # Removing it (the card's ×) cancels it; it no longer shows.
    r = head.post(f"/api/manual/own_makeup/{body['requestid']}/cancel").get_json()
    assert r['success'] and r['makeups'] == []
    assert _status(tx, body['requestid'])[0] == 'Cancelled'


@requires_db
def test_past_date_and_missing_reason_are_refused(tx):
    c = _own_class(tx)
    head = _client(c['username'])
    past = (date.today() - timedelta(days=1)).isoformat()
    r = head.post('/api/manual/own_makeup', json=_body(c, request_date=past))
    assert r.status_code == 400 and 'past' in r.get_json()['message']
    r = head.post('/api/manual/own_makeup', json=_body(c, reason='  '))
    assert r.status_code == 400 and 'reason' in r.get_json()['message']


@requires_db
def test_not_for_a_class_of_regular_faculty(tx):
    """Other faculty go through a make-up request; only Academic Heads' classes skip it."""
    c = _own_class(tx)
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    heads = list(app_module._academic_head_employee_numbers(cur))
    cur.execute("""
        SELECT cs.subjectcode, sc.sectionid FROM schedule sc
        JOIN schedule_version sv ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        WHERE sc.employeenumber IS NOT NULL AND NOT (sc.employeenumber = ANY(%s)) LIMIT 1
    """, [heads])
    other = cur.fetchone()
    if not other:
        pytest.skip('no other faculty class')
    head = _client(c['username'])
    r = head.post('/api/manual/own_makeup', json=_body(c, subject_code=other['subjectcode'],
                                                       section_id=other['sectionid']))
    assert r.status_code in (403, 404)
    assert head.get('/api/manual/own_makeups', query_string={
        'subject_code': other['subjectcode'], 'section_id': other['sectionid']}).get_json()['mine'] is False


@requires_db
def test_works_for_another_academic_heads_class(tx):
    """Two Academic Heads: either one may save a make-up for the other's class. It is
    recorded under the class's faculty and approved by the one who saved it."""
    c = _own_class(tx)
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT username, employeenumber FROM accounts WHERE isactive AND role = 'Academic Head'
                   AND employeenumber IS DISTINCT FROM %s LIMIT 1""", [c['employeenumber']])
    other = cur.fetchone()
    if not other:
        pytest.skip('only one Academic Head')
    r = _client(other['username']).post('/api/manual/own_makeup', json=_body(c))
    body = r.get_json()
    if r.status_code == 409:
        pytest.skip(f"that date is taken in the current data: {body.get('error')}")
    assert r.status_code == 200 and body['success'], body
    cur.execute("SELECT submitted_by, decided_by, status FROM class_meeting_request WHERE requestid = %s",
                [body['requestid']])
    row = cur.fetchone()
    assert (row['submitted_by'], row['decided_by'], row['status']) == (
        str(c['employeenumber']), str(other['employeenumber']), 'Approved')


@requires_db
def test_faculty_role_cannot_use_it(tx):
    c = _own_class(tx)
    r = _client(c['username'], role='Faculty').post('/api/manual/own_makeup', json=_body(c))
    assert r.status_code == 401


@requires_db
def test_editor_knows_her_employee_number_without_it_in_the_session(tx):
    c = _own_class(tx)
    html = _client(c['username']).get('/schedule/manual-editor?scheduler=local').get_data(as_text=True)
    assert f'data-my-emp-num="{c["employeenumber"]}"' in html
