"""
Request & Local Scheduling test matrix (faculty Make-up / Schedule Adjustment requests and
the Academic Head's decision), run against the real endpoints inside one rolled-back
transaction. IDs refer to the "Request and Local Scheduling Test Cases" sheet.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db

TIMES = ['07:30 AM', '08:00 AM', '08:30 AM', '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM',
         '11:30 AM', '12:00 PM', '12:30 PM', '01:00 PM', '01:30 PM', '02:00 PM', '02:30 PM', '03:00 PM',
         '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM', '05:30 PM', '06:00 PM', '06:30 PM', '07:00 PM',
         '07:30 PM', '08:00 PM', '08:30 PM', '09:00 PM', '09:30 PM']


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
        s.update(loggedin=True, role=role, username=username, last_seen=now, last_active=now,
                 sid=uuid.uuid4().hex)
    return c


@pytest.fixture
def cls(tx):
    """A faculty member's Published lecture class this semester, plus a free make-up slot."""
    cur = _cur(tx)
    cur.execute("""
        SELECT a.username, sc.employeenumber, cs.subjectcode, sc.sectionid, sc.scheduleid,
               ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid, sem.semenddate,
               COALESCE(cs.lecturehours, 0) + COALESCE(cs.laboratoryhours, 0) AS hours
        FROM schedule sc
        JOIN schedule_version sv ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
             AND sv.source IS DISTINCT FROM 'local'
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        JOIN accounts a ON a.employeenumber = sc.employeenumber AND a.isactive AND a.role = 'Faculty'
        WHERE ss.roomid IS NOT NULL AND COALESCE(cs.lecturehours, 0) > 0
          AND sem.semenddate > CURRENT_DATE + 21
        ORDER BY sc.scheduleid, ss.sessionid LIMIT 1
    """)
    c = cur.fetchone()
    if not c:
        pytest.skip('no Published class of a Faculty account this semester')
    # A free 1.5h slot next week (room + faculty + students), found with the real check.
    cur.execute("SELECT roomid FROM room WHERE COALESCE(isactive, TRUE) AND roomtype ILIKE 'lecture' ORDER BY roomid")
    rooms = [r['roomid'] for r in cur.fetchall()]
    for off in range(7, 14):
        d = date.today() + timedelta(days=off)
        if d.weekday() == 6:
            continue
        for si in (21, 23, 19, 0, 2):                  # 6:00 PM, 7:00 PM, 5:00 PM, 7:30 AM, 8:30 AM
            for rid in rooms[:15]:
                if app_module._makeup_submit_problems(c['subjectcode'], c['sectionid'], c['employeenumber'],
                                                      d.isoformat(), TIMES[si], TIMES[si + 3], rid) is None:
                    return dict(c, free=dict(date=d.isoformat(), start=TIMES[si], end=TIMES[si + 3], room=rid))
    pytest.skip('no free make-up slot found next week')


def _mu(cls, **over):
    f = cls['free']
    body = {'request_type': 'makeup', 'subject_code': cls['subjectcode'], 'section_id': cls['sectionid'],
            'request_date': f['date'], 'start_time': f['start'], 'end_time': f['end'], 'room_id': f['room'],
            'reason': 'Missed class due to a university event'}
    body.update(over)
    return body


def _submit(cls, **over):
    return _client(cls['username'], 'Faculty').post('/api/faculty/submit_request', json=_mu(cls, **over))


def _msg(r):
    j = r.get_json() or {}
    return f"{j.get('error')} {j.get('message')} {j.get('conflicts')}"


# ── Category 1: form-level validation (server side; the form checks the same first) ──

@requires_db
def test_fsv01_past_date_is_refused(tx, cls):
    r = _submit(cls, request_date=(date.today() - timedelta(days=1)).isoformat())
    assert r.status_code == 400 and 'past' in _msg(r)


@requires_db
def test_fsv02_time_outside_school_hours_is_refused(tx, cls):
    r = _submit(cls, start_time='08:00 PM', end_time='09:30 PM')
    assert r.status_code == 400 and '9:00 PM' in _msg(r).replace('09:00', '9:00')


@requires_db
def test_fsv03_duration_longer_than_a_session_is_refused(tx, cls):
    r = _submit(cls, start_time='07:30 AM', end_time='09:00 PM')
    assert r.status_code == 400


@requires_db
def test_fsv04_empty_reason_is_refused(tx, cls):
    r = _submit(cls, reason='   ')
    assert r.status_code == 400 and 'reason' in _msg(r).lower()


@requires_db
def test_fsv09_missing_subject_is_refused(tx, cls):
    r = _submit(cls, subject_code='')
    assert r.status_code == 400 and 'subject' in _msg(r).lower()


@requires_db
def test_fsv10_special_characters_are_kept_and_shown_safely(tx, cls):
    reason = 'Math@101! <b>bold</b> & "quotes"'
    r = _submit(cls, reason=reason)
    assert r.status_code == 200, _msg(r)
    cur = _cur(tx)
    cur.execute("SELECT reason FROM class_meeting_request WHERE submitted_by = %s ORDER BY requestid DESC LIMIT 1",
                [cls['employeenumber']])
    assert cur.fetchone()['reason'] == reason
    hub = open('templates/academic/requests_hub.html', encoding='utf-8').read()
    assert '${_rhubEsc(req.reason)}' in hub and '${_rhubEsc(req.notes)}' in hub


# ── Category 2: scheduling conflicts ──

@requires_db
def test_slc01_overlapping_own_class_is_refused(tx, cls):
    # The make-up on the class's own weekday, at its own time: the faculty already teaches then.
    d = date.today() + timedelta(days=7)
    while d.strftime('%A') != cls['daydesc']:
        d += timedelta(days=1)
    r = _submit(cls, request_date=d.isoformat(), start_time=TIMES[cls['starttimeid'] - 1],
                end_time=TIMES[min(cls['starttimeid'] + 2, cls['endtimeid']) - 1], room_id=cls['free']['room'])
    assert r.status_code == 409
    kinds = {c['type'] for c in r.get_json()['conflicts']}
    assert 'faculty' in kinds


@requires_db
def test_slc02_room_used_by_another_class_is_refused(tx, cls):
    cur = _cur(tx)
    d = date.fromisoformat(cls['free']['date'])
    cur.execute("""
        SELECT ss.roomid, ss.starttimeid FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid AND sv.status = 'Published'
        JOIN schedule sc ON sc.scheduleid = sv.scheduleid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        WHERE ss.daydesc = %s AND ss.roomid IS NOT NULL AND sc.employeenumber IS DISTINCT FROM %s
          AND ss.endtimeid - ss.starttimeid >= 3 LIMIT 1
    """, [d.strftime('%A'), cls['employeenumber']])
    occ = cur.fetchone()
    if not occ:
        pytest.skip('no other class that day')
    r = _submit(cls, room_id=occ['roomid'], start_time=TIMES[occ['starttimeid'] - 1],
                end_time=TIMES[occ['starttimeid'] + 2])
    assert r.status_code == 409 and 'room' in {c['type'] for c in r.get_json()['conflicts']}


@requires_db
def test_slc05_lab_subject_in_a_lecture_room_is_a_notice_not_a_block(tx, cls):
    """Sometimes no Laboratory room is free: the make-up is submitted, with a notice for
    the faculty and an advisory for the Academic Head."""
    cur = _cur(tx)
    # Make the class's subject lab-only for this test (rolled back).
    cur.execute("""UPDATE curriculumsubject SET lecturehours = 0, laboratoryhours = %s
                   WHERE curriculumsubjectid = (SELECT curriculumsubjectid FROM schedule WHERE scheduleid = %s)""",
                [cls['hours'], cls['scheduleid']])
    tx.handle().commit()
    r = _submit(cls)
    body = r.get_json()
    assert r.status_code == 200 and body['success'], _msg(r)
    assert any('laboratory subject' in a for a in body['advisories'])
    cur.execute("SELECT requestid FROM class_meeting_request WHERE submitted_by = %s AND status = 'Pending' "
                "ORDER BY requestid DESC LIMIT 1", [cls['employeenumber']])
    summary, err = app_module._request_conflict_summary('makeup', cur.fetchone()['requestid'])
    assert not err and summary['all_ok']                                 # still approvable
    assert any(w['rule'] == 'HC13' for w in summary['warnings'])         # advisory for the Head


@requires_db
def test_slc07_overlapping_pending_request_is_refused(tx, cls):
    assert _submit(cls).status_code == 200
    i = TIMES.index(cls['free']['start'])
    r = _submit(cls, start_time=TIMES[i + 1], end_time=TIMES[i + 4])
    assert r.status_code == 409 and 'pending' in {c['type'] for c in r.get_json()['conflicts']}


@requires_db
def test_slc03_approval_rechecks_and_refuses_a_now_conflicting_request(tx, cls):
    assert _submit(cls).status_code == 200
    cur = _cur(tx)
    cur.execute("SELECT requestid FROM class_meeting_request WHERE submitted_by = %s AND status = 'Pending' "
                "ORDER BY requestid DESC LIMIT 1", [cls['employeenumber']])
    req_id = cur.fetchone()['requestid']
    # Another approved make-up takes the room at that time after submission.
    cur.execute("""INSERT INTO class_meeting_request (scheduleid, requested_date, new_starttimeid, new_endtimeid,
                       new_roomid, reason, submitted_by, status, reviewed_by, reviewed_at)
                   SELECT scheduleid, requested_date, new_starttimeid, new_endtimeid, new_roomid, 'other',
                          submitted_by, 'Approved', submitted_by, NOW()
                   FROM class_meeting_request WHERE requestid = %s""", [req_id])
    tx.handle().commit()
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' LIMIT 1")
    head = _client(cur.fetchone()['username'], 'Academic Head')
    r = head.post('/api/requests/decide', json={'type': 'makeup', 'id': req_id, 'decision': 'Approved'})
    assert r.status_code == 409 and r.get_json()['code'] == 'REQUEST_NO_LONGER_VALID'
    cur.execute("SELECT status FROM class_meeting_request WHERE requestid = %s", [req_id])
    assert cur.fetchone()['status'] == 'Pending'


# ── Category 3: workflow & data integrity ──

def _pending_and_head(tx, cls):
    assert _submit(cls).status_code == 200
    cur = _cur(tx)
    cur.execute("SELECT requestid FROM class_meeting_request WHERE submitted_by = %s AND status = 'Pending' "
                "ORDER BY requestid DESC LIMIT 1", [cls['employeenumber']])
    req_id = cur.fetchone()['requestid']
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' LIMIT 1")
    return req_id, _client(cur.fetchone()['username'], 'Academic Head')


@requires_db
def test_wdi05_a_request_cannot_be_decided_twice(tx, cls):
    req_id, head = _pending_and_head(tx, cls)
    assert head.post('/api/requests/decide', json={'type': 'makeup', 'id': req_id, 'decision': 'Approved'}).status_code == 200
    r = head.post('/api/requests/decide', json={'type': 'makeup', 'id': req_id, 'decision': 'Rejected', 'remarks': 'x'})
    assert r.status_code == 409 and r.get_json()['code'] == 'REQUEST_ALREADY_DECIDED'
    cur = _cur(tx)
    cur.execute("SELECT status FROM class_meeting_request WHERE requestid = %s", [req_id])
    assert cur.fetchone()['status'] == 'Approved'


@requires_db
def test_wdi06_failed_approval_leaves_the_request_pending(tx, cls, monkeypatch):
    req_id, head = _pending_and_head(tx, cls)

    def _boom(*a, **k):
        raise RuntimeError('database went away')
    monkeypatch.setattr(app_module, '_request_conflict_summary', _boom)
    r = head.post('/api/requests/decide', json={'type': 'makeup', 'id': req_id, 'decision': 'Approved'})
    body = r.get_json()
    assert r.status_code == 500 and body['code'] == 'DECISION_NOT_SAVED' and 'still Pending' in body['error']
    cur = _cur(tx)
    cur.execute("SELECT status FROM class_meeting_request WHERE requestid = %s", [req_id])
    assert cur.fetchone()['status'] == 'Pending'


@requires_db
def test_wdi07_the_decision_shows_in_the_faculty_inbox(tx, cls):
    req_id, head = _pending_and_head(tx, cls)
    assert head.post('/api/requests/decide', json={'type': 'makeup', 'id': req_id, 'decision': 'Approved'}).status_code == 200
    page = _client(cls['username'], 'Faculty').get('/faculty_dashboard').get_data(as_text=True)
    assert 'Make-up class approved' in page


@requires_db
def test_adjustment_into_an_occupied_room_is_refused_and_not_kept(tx, cls):
    cur = _cur(tx)
    cur.execute("""
        SELECT ss.sessionid, ss.starttimeid, ss.endtimeid FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid AND sv.status = 'Published'
        WHERE sv.scheduleid = %s AND ss.daydesc = %s AND ss.starttimeid = %s LIMIT 1
    """, [cls['scheduleid'], cls['daydesc'], cls['starttimeid']])
    mine = cur.fetchone()
    length = mine['endtimeid'] - mine['starttimeid']
    # Another faculty's class with a room, long enough to hold this meeting.
    cur.execute("""
        SELECT ss.daydesc, ss.starttimeid, ss.roomid FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid AND sv.status = 'Published'
        JOIN schedule sc ON sc.scheduleid = sv.scheduleid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        WHERE sc.employeenumber IS DISTINCT FROM %s AND ss.roomid IS NOT NULL
          AND ss.endtimeid - ss.starttimeid >= %s AND ss.daydesc <> 'Sunday' LIMIT 1
    """, [cls['employeenumber'], length])
    occ = cur.fetchone()
    if not occ:
        pytest.skip('no other class to collide with')
    cur.execute("SELECT COUNT(*) AS n FROM schedule_change_request WHERE submitted_by = %s", [cls['employeenumber']])
    before = cur.fetchone()['n']
    r = _client(cls['username'], 'Faculty').post('/api/faculty/submit_request', json={
        'request_type': 'adjustment', 'subject_code': cls['subjectcode'], 'section_id': cls['sectionid'],
        'reason': 'Room change', 'meetings': [{
            'official_sessionid': mine['sessionid'], 'day': occ['daydesc'],
            'start_time': TIMES[occ['starttimeid'] - 1], 'end_time': TIMES[occ['starttimeid'] - 1 + length],
            'room_id': occ['roomid']}]})
    assert r.status_code == 409 and r.get_json()['conflicts'], _msg(r)
    cur.execute("SELECT COUNT(*) AS n FROM schedule_change_request WHERE submitted_by = %s", [cls['employeenumber']])
    assert cur.fetchone()['n'] == before
