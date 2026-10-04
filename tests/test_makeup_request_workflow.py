"""
Make-up class request workflow, end to end against the local ASDBv11 database.

    Faculty submits → server validates → Pending
    Academic Head approves → Approved + exactly one date-specific
        'makeup_class' row in schedule_exception_log; the recurring Published
        schedule (schedule_sessions) is never modified
    Academic Head rejects → Rejected, no exception, schedule unchanged

Fixtures create their own schedule/schedule_version/schedule_sessions rows
(on Sundays, which the live data doesn't use) from existing faculty, subject,
section and room records, and delete everything they created afterwards.
Skipped automatically when the database isn't reachable.
"""
import threading
from datetime import date, timedelta

import pytest
import psycopg2
from psycopg2.extras import RealDictCursor

from conftest import requires_db, DB_AVAILABLE

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module
    import makeup_requests
    from config import Config

AH_USER = '94045'          # Academic Head account (also a faculty record)
START, END = '08:00 AM', '11:00 AM'   # 3h — fixture subjects are 3h subjects


def _connect():
    return psycopg2.connect(dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
                            host=Config.DB_HOST, port=Config.DB_PORT,
                            cursor_factory=RealDictCursor)


def _q(sql, args=(), one=False):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return (rows[0] if rows else None) if one else rows
    finally:
        conn.close()


class World:
    """Test data: one faculty-owned class (schedule S) plus helpers to add
    temporary schedules that occupy the make-up slot."""

    def __init__(self):
        self.schedules = []
        sem = _q("SELECT semesterid, semstartdate, semenddate FROM semester WHERE isactive LIMIT 1", one=True)
        self.sem = sem['semesterid']
        d = date.today() + timedelta(days=1)
        while d.weekday() != 6:                       # Sunday
            d += timedelta(days=1)
        if sem['semenddate'] and d > sem['semenddate']:
            pytest.skip('no future Sunday left in the active semester')
        self.date = d
        self.other_sem = _q("SELECT semesterid FROM semester WHERE semesterid <> %s ORDER BY semesterid LIMIT 1",
                            [self.sem], one=True)['semesterid']

        sunday_free = """NOT EXISTS (
            SELECT 1 FROM schedule_sessions ss JOIN schedule_version sv ON sv.versionid = ss.versionid
            JOIN schedule s2 ON s2.scheduleid = sv.scheduleid
            WHERE ss.daydesc = 'Sunday' AND s2.semesterid = {sem} AND {cond})"""
        fac = _q(f"""
            SELECT a.username, a.employeenumber FROM accounts a
            JOIN faculty f ON f.employeenumber = a.employeenumber
            WHERE a.role = 'Faculty' AND a.isactive AND a.employeenumber <> %s
              AND {sunday_free.format(sem=self.sem, cond='s2.employeenumber = a.employeenumber')}
            ORDER BY a.employeenumber LIMIT 1""", [AH_USER], one=True)
        if not fac:
            pytest.skip('no active Faculty account available for the test')
        self.fac_user, self.fac = fac['username'], fac['employeenumber']
        self.fac2 = _q(f"""SELECT f.employeenumber FROM faculty f WHERE f.employeenumber NOT IN (%s, %s)
            AND {sunday_free.format(sem=self.sem, cond='s2.employeenumber = f.employeenumber')}
            ORDER BY 1 LIMIT 1""", [self.fac, AH_USER], one=True)['employeenumber']
        secs = _q(f"""SELECT sectionid, programyearlevelid FROM sections sec WHERE isactive
            AND {sunday_free.format(sem=self.sem, cond='s2.sectionid = sec.sectionid')}
            ORDER BY sectionid LIMIT 2""")
        self.section, self.section2 = secs[0]['sectionid'], secs[1]['sectionid']
        rooms = _q(f"""SELECT roomid FROM room r WHERE
            {sunday_free.format(sem=self.sem, cond='ss.roomid = r.roomid')}
            AND NOT EXISTS (SELECT 1 FROM local_arrangement_sessions l WHERE l.roomid = r.roomid)
            ORDER BY roomid LIMIT 2""")
        self.room, self.room2 = rooms[0]['roomid'], rooms[1]['roomid']
        self._subjects = [r['curriculumsubjectid'] for r in _q("""
            SELECT curriculumsubjectid FROM curriculumsubject
            WHERE COALESCE(lecturehours,0) + COALESCE(laboratoryhours,0) = 3
            ORDER BY curriculumsubjectid LIMIT 400""")]
        self.tid = {r['v']: r['timeid'] for r in _q(
            "SELECT timeid, TO_CHAR(timevalue,'HH:MI AM') v FROM timeslot")}

        # The faculty's own class: Wednesday 08:00–11:00 in room2.
        self.S = self.add_schedule(self.fac, self.section, 'Wednesday', self.room2)
        self.subject_code = _q("""SELECT cs.subjectcode FROM schedule sc JOIN curriculumsubject cs
            ON cs.curriculumsubjectid = sc.curriculumsubjectid WHERE sc.scheduleid = %s""",
                               [self.S], one=True)['subjectcode']

    def add_schedule(self, faculty, section, day, room, semester=None, status='Published',
                     start=START, end=END):
        semester = semester or self.sem
        for cid in self._subjects:
            if not _q("SELECT 1 FROM schedule WHERE curriculumsubjectid=%s AND sectionid=%s AND semesterid=%s",
                      [cid, section, semester]):
                # subject codes must be unique for this section so subject+section → S is unambiguous
                code = _q("SELECT subjectcode FROM curriculumsubject WHERE curriculumsubjectid=%s", [cid], one=True)
                if _q("""SELECT 1 FROM schedule sc JOIN curriculumsubject cs ON cs.curriculumsubjectid=sc.curriculumsubjectid
                         WHERE cs.subjectcode=%s AND sc.sectionid=%s""", [code['subjectcode'], section]):
                    continue
                break
        else:
            pytest.skip('no free subject for a fixture schedule')
        sid = _q("""INSERT INTO schedule (curriculumsubjectid, sectionid, employeenumber, semesterid)
                    VALUES (%s,%s,%s,%s) RETURNING scheduleid""", [cid, section, faculty, semester], one=True)['scheduleid']
        self.schedules.append(sid)
        vid = _q("""INSERT INTO schedule_version (scheduleid, version_number, status)
                    VALUES (%s, 1, %s) RETURNING versionid""", [sid, status], one=True)['versionid']
        _q("""INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid)
              VALUES (%s,%s,%s,%s,%s)""", [vid, day, self.tid[start], self.tid[end], room])
        return sid

    def sessions_snapshot(self):
        return _q("""SELECT ss.sessionid, ss.versionid, ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid
                     FROM schedule_sessions ss JOIN schedule_version sv ON sv.versionid = ss.versionid
                     WHERE sv.scheduleid = %s ORDER BY ss.sessionid""", [self.S])

    def cleanup(self):
        if not self.schedules:
            return
        ids = self.schedules
        reqs = [r['requestid'] for r in _q(
            "SELECT requestid FROM class_meeting_request WHERE scheduleid = ANY(%s)", [ids])]
        _q("DELETE FROM schedule_exception_log WHERE scheduleid = ANY(%s)", [ids])
        _q("DELETE FROM class_meeting_request WHERE scheduleid = ANY(%s)", [ids])
        for rid in reqs:
            _q("DELETE FROM activity_log WHERE details LIKE %s", [f'MAKEUP request #{rid} %'])
        _q("DELETE FROM schedule WHERE scheduleid = ANY(%s)", [ids])   # cascades versions/sessions


@pytest.fixture
def world():
    w = World()
    try:
        yield w
    finally:
        w.cleanup()


def _client(username, role, empno):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['loggedin'] = True
        s['username'] = username
        s['role'] = role
        s['employeenumber'] = empno
    return c


def _faculty(w):
    return _client(w.fac_user, 'Faculty', w.fac)


def _head():
    return _client(AH_USER, 'Academic Head', AH_USER)


def _payload(w, **over):
    p = {'request_type': 'makeup', 'subject_code': w.subject_code, 'section_id': str(w.section),
         'room_id': str(w.room), 'request_date': w.date.isoformat(),
         'start_time': START, 'end_time': END, 'reason': 'Missed class due to university event',
         'note': 'test'}
    p.update(over)
    return p


def _submit(w, **over):
    return _faculty(w).post('/api/faculty/submit_request', json=_payload(w, **over))


def _decide(rid, decision, remarks=''):
    return _head().post('/api/requests/decide',
                        json={'type': 'makeup', 'id': rid, 'decision': decision, 'remarks': remarks})


def _req(rid):
    return _q("SELECT * FROM class_meeting_request WHERE requestid = %s", [rid], one=True)


def _exceptions(rid):
    return _q("SELECT * FROM schedule_exception_log WHERE source_type='makeup_class' AND source_requestid=%s", [rid])


def _new_pending(w, **over):
    r = _submit(w, **over)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['requestid']


# ── Submission ──────────────────────────────────────────────────────────────

def test_valid_request_is_saved_as_pending(world):
    rid = _new_pending(world)
    row = _req(rid)
    assert row['status'] == 'Pending'
    assert row['requested_date'] == world.date
    assert row['new_roomid'] == world.room
    assert row['reviewed_by'] is None and row['reviewed_at'] is None


@pytest.mark.parametrize('reason', ['', '    ', None])
def test_blank_or_whitespace_reason_is_rejected_cleanly(world, reason):
    r = _submit(world, reason=reason)
    body = r.get_json()
    assert r.status_code == 400
    assert body['error'] == 'validation_error'
    assert 'reason' in body['message'].lower()
    assert 'psycopg' not in str(body).lower() and 'chk_' not in str(body)
    assert not _q("SELECT 1 FROM class_meeting_request WHERE scheduleid=%s", [world.S])


@pytest.mark.parametrize('start,end', [('25:00 PM', END), (START, 'nope'), (END, START), (START, START)])
def test_invalid_time_is_rejected(world, start, end):
    r = _submit(world, start_time=start, end_time=end)
    assert r.status_code == 400 and r.get_json()['error'] == 'validation_error'


def test_duration_mismatch_is_rejected(world):
    r = _submit(world, end_time='09:00 AM')
    assert r.status_code == 400
    assert 'requires 3h' in r.get_json()['message']


def test_invalid_and_past_dates_are_rejected(world):
    assert _submit(world, request_date='2026-02-30').status_code == 400
    past = (date.today() - timedelta(days=1)).isoformat()
    assert _submit(world, request_date=past).status_code == 400


def test_request_for_someone_elses_class_is_forbidden(world):
    other = world.add_schedule(world.fac2, world.section2, 'Monday', world.room2)
    code = _q("""SELECT cs.subjectcode FROM schedule sc JOIN curriculumsubject cs
                 ON cs.curriculumsubjectid = sc.curriculumsubjectid WHERE sc.scheduleid=%s""", [other], one=True)
    r = _submit(world, subject_code=code['subjectcode'], section_id=str(world.section2))
    assert r.status_code == 403


def _conflict_types(resp):
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body['error'] == 'schedule_conflict'
    return {c['type'] for c in body['conflicts']}


def test_faculty_conflict_is_rejected(world):
    world.add_schedule(world.fac, world.section2, 'Sunday', world.room2)
    assert _conflict_types(_submit(world)) == {'faculty'}


def test_section_conflict_is_rejected(world):
    world.add_schedule(world.fac2, world.section, 'Sunday', world.room2)
    assert _conflict_types(_submit(world)) == {'section'}


def test_room_conflict_is_rejected(world):
    world.add_schedule(world.fac2, world.section2, 'Sunday', world.room)
    assert _conflict_types(_submit(world)) == {'room'}


def test_other_semester_schedule_is_not_a_conflict(world):
    # Same faculty, same section, same room, same weekday/time — but another semester.
    world.add_schedule(world.fac, world.section, 'Sunday', world.room, semester=world.other_sem)
    assert _submit(world).status_code == 200


def test_draft_schedule_is_not_a_hard_conflict(world):
    world.add_schedule(world.fac2, world.section2, 'Sunday', world.room, status='Draft')
    assert _submit(world).status_code == 200


def test_faculty_edit_is_validated_the_same_way(world):
    rid = _new_pending(world)
    c = _faculty(world)
    r = c.put(f'/api/faculty/my_requests/makeup/{rid}', json=_payload(world, reason='   '))
    assert r.status_code == 400
    world.add_schedule(world.fac2, world.section2, 'Sunday', world.room2)
    r = c.put(f'/api/faculty/my_requests/makeup/{rid}', json=_payload(world, room_id=str(world.room2)))
    assert r.status_code == 409 and r.get_json()['conflicts'][0]['type'] == 'room'
    assert _req(rid)['new_roomid'] == world.room          # unchanged


# ── Approval ────────────────────────────────────────────────────────────────

def test_approval_creates_one_dated_exception_and_leaves_weekly_schedule_alone(world):
    rid = _new_pending(world)
    before = world.sessions_snapshot()
    total_before = _q("SELECT COUNT(*) n FROM schedule_sessions", one=True)['n']

    r = _decide(rid, 'Approved')
    assert r.status_code == 200, r.get_json()

    row = _req(rid)
    assert row['status'] == 'Approved'
    assert row['reviewed_by'] == AH_USER and row['reviewed_at'] is not None

    exc = _exceptions(rid)
    assert len(exc) == 1
    e = exc[0]
    assert e['exception_date'] == world.date
    assert e['scheduleid'] == world.S
    assert e['starttimeid'] == world.tid[START] and e['endtimeid'] == world.tid[END]
    assert e['roomid'] == world.room
    assert e['employeenumber'] == world.fac
    assert e['approved_by'] == AH_USER and e['semesterid'] == world.sem
    assert r.get_json()['exception_logid'] == e['logid']

    # No recurring Sunday slot; the Wednesday class is untouched.
    assert world.sessions_snapshot() == before
    assert [s['daydesc'] for s in before] == ['Wednesday']
    assert _q("SELECT COUNT(*) n FROM schedule_sessions", one=True)['n'] == total_before


def test_rejection_creates_nothing_and_keeps_remarks(world):
    rid = _new_pending(world)
    before = world.sessions_snapshot()
    r = _decide(rid, 'Rejected', 'Room reserved for an event')
    assert r.status_code == 200
    row = _req(rid)
    assert row['status'] == 'Rejected'
    assert row['reviewed_by'] == AH_USER and row['reviewed_at'] is not None
    assert row['remarks'] == 'Room reserved for an event'
    assert _exceptions(rid) == []
    assert world.sessions_snapshot() == before


@pytest.mark.parametrize('first,second', [
    ('Approved', 'Rejected'), ('Approved', 'Approved'),
    ('Rejected', 'Approved'), ('Rejected', 'Rejected'),
])
def test_decided_requests_are_immutable(world, first, second):
    rid = _new_pending(world)
    assert _decide(rid, first).status_code == 200
    snapshot = _req(rid)
    r = _decide(rid, second)
    assert r.status_code == 409
    assert r.get_json()['error'] == 'invalid_state'
    assert r.get_json()['current_status'] == first
    after = _req(rid)
    assert after['status'] == first and after['reviewed_at'] == snapshot['reviewed_at']
    assert len(_exceptions(rid)) == (1 if first == 'Approved' else 0)


def test_unknown_request_returns_404(world):
    assert _decide(987654321, 'Approved').status_code == 404


def test_concurrent_approvals_only_one_wins(world):
    rid = _new_pending(world)
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt():
        conn = _connect()
        try:
            barrier.wait()
            makeup_requests.decide(conn, request_id=rid, decision='Approved', reviewer=AH_USER)
            outcomes.append(200)
        except makeup_requests.MakeupError as e:
            outcomes.append(e.status)
        finally:
            conn.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(outcomes) == [200, 409]
    assert len(_exceptions(rid)) == 1


def test_approval_rechecks_current_state(world):
    rid = _new_pending(world)
    # The room gets taken AFTER submission, before approval.
    world.add_schedule(world.fac2, world.section2, 'Sunday', world.room)
    r = _decide(rid, 'Approved')
    assert r.status_code == 409
    assert r.get_json()['error'] == 'schedule_conflict'
    assert {c['type'] for c in r.get_json()['conflicts']} == {'room'}
    assert _req(rid)['status'] == 'Pending'
    assert _exceptions(rid) == []


def test_approved_makeup_blocks_a_second_one_on_the_same_date_only(world):
    rid = _new_pending(world)
    assert _decide(rid, 'Approved').status_code == 200
    # Another class wants the same room at the same time on the same date.
    other = world.add_schedule(world.fac2, world.section2, 'Monday', world.room2)
    rid2 = _q("""INSERT INTO class_meeting_request (scheduleid, requested_date, new_starttimeid,
                 new_endtimeid, new_roomid, reason, submitted_by, status)
                 VALUES (%s,%s,%s,%s,%s,'x',%s,'Pending') RETURNING requestid""",
              [other, world.date, world.tid[START], world.tid[END], world.room, world.fac2], one=True)['requestid']
    r = _decide(rid2, 'Approved')
    assert r.status_code == 409 and r.get_json()['conflicts'][0]['source'] == 'makeup'
    # One week later the same slot is free: the make-up is not a weekly class.
    _q("UPDATE class_meeting_request SET requested_date = %s WHERE requestid = %s",
       [world.date + timedelta(days=7), rid2])
    if world.date + timedelta(days=7) <= _q("SELECT semenddate FROM semester WHERE semesterid=%s",
                                            [world.sem], one=True)['semenddate']:
        assert _decide(rid2, 'Approved').status_code == 200


# ── Read-only conflict endpoints ────────────────────────────────────────────

def test_validate_endpoint_reports_current_conflicts(world):
    rid = _new_pending(world)
    v = _head().get(f'/api/requests/validate?type=makeup&id={rid}').get_json()
    assert v['success'] and v['all_ok'] and v['section_free'] and v['status'] == 'Pending'
    world.add_schedule(world.fac2, world.section, 'Sunday', world.room2, semester=world.other_sem)
    assert _head().get(f'/api/requests/validate?type=makeup&id={rid}').get_json()['all_ok']
    world.add_schedule(world.fac2, world.section, 'Sunday', world.room2)
    v = _head().get(f'/api/requests/validate?type=makeup&id={rid}').get_json()
    assert not v['all_ok'] and not v['section_free'] and v['room_available']


def test_faculty_live_check_uses_the_exact_date(world):
    world.add_schedule(world.fac2, world.section2, 'Sunday', world.room)
    params = {'date': world.date.isoformat(), 'start_time': START, 'end_time': END,
              'section_id': world.section, 'room_id': world.room, 'subject_code': world.subject_code}
    d = _faculty(world).get('/api/faculty/check_request_conflicts', query_string=params).get_json()
    assert d['room_conflict'] and not d['faculty_conflict'] and not d['section_conflict']
    params['room_id'] = world.room2
    d = _faculty(world).get('/api/faculty/check_request_conflicts', query_string=params).get_json()
    assert not (d['room_conflict'] or d['faculty_conflict'] or d['section_conflict'])
