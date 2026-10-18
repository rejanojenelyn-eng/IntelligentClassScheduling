"""Schedule Adjustment that moves one or several meetings of a class.

A request may move several Official meetings of one class (one row per moved meeting,
sharing request_group). The new meetings must keep the SAME total hours (any split:
Mon 2h + Wed 3h -> 2.5h + 2.5h, or one continuous block sent as back-to-back pieces).
The Academic Head approves the whole request as a Local arrangement or into the
Official schedule (republished through Publish, every hard constraint checked).

Real database, ONE transaction always rolled back (tests/_shared_tx.py).
"""
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def tx(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    monkeypatch.setattr(app_module, '_request_tables_ensured', False, raising=False)
    try:
        yield t
    finally:
        t.close()


def _q(tx, sql, params=None):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params or [])
    return cur.fetchall()


@pytest.fixture
def cls(tx):
    """A Published class of the active semester with at least two meetings, whose
    faculty has a login: its meetings, section, faculty and an Academic Head login."""
    app_module._ensure_request_tables()
    rows = _q(tx, """
        SELECT sc.scheduleid, sc.sectionid, sc.employeenumber, cs.subjectcode, a.username,
               COUNT(*) AS n
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid AND sv.status = 'Published'
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        JOIN accounts a ON a.employeenumber = sc.employeenumber AND a.isactive AND a.role = 'Faculty'
        -- free to request: no approved Local arrangement and no pending request on its meetings
        WHERE NOT EXISTS (SELECT 1 FROM local_arrangement_sessions las
                          JOIN local_arrangement la ON la.arrangementid = las.arrangementid
                          WHERE las.official_sessionid = ss.sessionid
                            AND la.status = 'Published' AND la.is_active)
          AND NOT EXISTS (SELECT 1 FROM schedule_change_request q
                          WHERE q.official_sessionid = ss.sessionid AND q.status = 'Pending')
        GROUP BY 1, 2, 3, 4, 5 HAVING COUNT(*) = 2
        ORDER BY sc.scheduleid LIMIT 1
    """)
    head = _q(tx, "SELECT username, employeenumber FROM accounts WHERE isactive AND role = 'Academic Head' LIMIT 1")
    if not rows or not head:
        pytest.skip('no two-meeting Published class with a faculty login / no Academic Head')
    c = rows[0]
    meets = _q(tx, """
        SELECT ss.sessionid, ss.daydesc, ss.roomid, ss.starttimeid, ss.endtimeid,
               TO_CHAR(t1.timevalue, 'HH12:MI AM') AS st, TO_CHAR(t2.timevalue, 'HH12:MI AM') AS en
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid AND sv.status = 'Published'
        JOIN timeslot t1 ON t1.timeid = ss.starttimeid JOIN timeslot t2 ON t2.timeid = ss.endtimeid
        WHERE sv.scheduleid = %s ORDER BY ss.sessionid
    """, [c['scheduleid']])
    return dict(c, meets=meets, head=head[0])


def _client(role, username, emp):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role=role, username=username, employeenumber=emp,
                 account_setup_complete=True, must_change_password=False)
    return c


SLOTS = ['07:30 AM', '08:00 AM', '08:30 AM', '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM',
         '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '01:00 PM', '01:30 PM', '02:00 PM',
         '02:30 PM', '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM', '05:30 PM',
         '06:00 PM', '06:30 PM', '07:00 PM', '07:30 PM', '08:00 PM', '08:30 PM', '09:00 PM']


def _shift_end(t, half_hours):
    return SLOTS[SLOTS.index(t) + half_hours]


def _resplit(c):
    """Same days/rooms, the 1st meeting 30 min longer and the 2nd 30 min shorter:
    the total stays the same (a re-split)."""
    a, b = c['meets']
    return [
        {'official_sessionid': a['sessionid'], 'day': a['daydesc'], 'start_time': a['st'],
         'end_time': _shift_end(a['en'], 1), 'room_id': a['roomid']},
        {'official_sessionid': b['sessionid'], 'day': b['daydesc'], 'start_time': b['st'],
         'end_time': _shift_end(b['en'], -1), 'room_id': b['roomid']},
    ]


def _submit(c, meetings, reason='multi-meeting test'):
    fac = _client('Faculty', c['username'], c['employeenumber'])
    return fac.post('/api/faculty/submit_request', json={
        'request_type': 'adjustment', 'subject_code': c['subjectcode'], 'section_id': c['sectionid'],
        'reason': reason, 'meetings': meetings}).get_json()


def _rows(tx, reason='multi-meeting test'):
    return _q(tx, "SELECT requestid, request_group, status, applied_to FROM schedule_change_request "
                  "WHERE reason = %s ORDER BY requestid", [reason])


def test_resplit_is_one_request_with_one_row_per_moved_meeting(tx, cls):
    res = _submit(cls, _resplit(cls))
    assert res['success'], res
    rows = _rows(tx)
    assert len(rows) == 2 and rows[0]['request_group'] and rows[0]['request_group'] == rows[1]['request_group']


def test_total_hours_must_stay_the_same(tx, cls):
    m = _resplit(cls)
    m[1]['end_time'] = cls['meets'][1]['en']       # 2nd keeps its length: total grows by 30 min
    res = _submit(cls, m)
    assert not res['success'] and 'The hours must stay the same' in res['error']
    assert _rows(tx) == []


def test_new_meetings_of_one_class_cannot_overlap(tx, cls):
    a, b = cls['meets']
    ha, hb = a['endtimeid'] - a['starttimeid'], b['endtimeid'] - b['starttimeid']
    m = [{'official_sessionid': a['sessionid'], 'day': 'Thursday', 'start_time': '08:00 AM',
          'end_time': SLOTS[1 + ha], 'room_id': a['roomid']},
         {'official_sessionid': b['sessionid'], 'day': 'Thursday', 'start_time': '08:30 AM',
          'end_time': SLOTS[2 + hb], 'room_id': b['roomid']}]
    res = _submit(cls, m)
    assert not res['success'] and 'overlap' in res['error']


def test_combined_meeting_is_sent_as_back_to_back_pieces(tx, cls):
    a, b = cls['meets']
    ha, hb = a['endtimeid'] - a['starttimeid'], b['endtimeid'] - b['starttimeid']
    mid = SLOTS[1 + ha]
    m = [{'official_sessionid': a['sessionid'], 'day': 'Thursday', 'start_time': '08:00 AM',
          'end_time': mid, 'room_id': a['roomid']},
         {'official_sessionid': b['sessionid'], 'day': 'Thursday', 'start_time': mid,
          'end_time': SLOTS[SLOTS.index(mid) + hb], 'room_id': a['roomid']}]
    assert _submit(cls, m)['success']
    assert len(_rows(tx)) == 2


def test_requests_hub_and_my_requests_show_one_item(tx, cls):
    assert _submit(cls, _resplit(cls))['success']
    head = _client('Academic Head', cls['head']['username'], cls['head']['employeenumber'])
    items = [r for r in head.get('/api/requests/list').get_json()['requests']
             if r['req_type'] == 'adjustment' and r.get('reason') == 'multi-meeting test']
    assert len(items) == 1 and len(items[0]['members']) == 2
    assert {m['orig_day'] for m in items[0]['members']} == {x['daydesc'] for x in cls['meets']}
    fac = _client('Faculty', cls['username'], cls['employeenumber'])
    mine = [r for r in fac.get('/api/faculty/my_requests').get_json()['requests']
            if r.get('reason') == 'multi-meeting test']
    assert len(mine) == 1 and len(mine[0]['members']) == 2


def _approve(tx, cls, apply_to):
    assert _submit(cls, _resplit(cls))['success']
    rid = _rows(tx)[0]['requestid']
    head = _client('Academic Head', cls['head']['username'], cls['head']['employeenumber'])
    v = head.get(f'/api/requests/validate?type=adjustment&id={rid}').get_json()
    if not v.get('all_ok'):
        pytest.skip(f'the re-split conflicts with live data: {v.get("conflicts")}')
    return head.post('/api/requests/decide', json={
        'type': 'adjustment', 'id': rid, 'decision': 'Approved', 'remarks': '', 'apply_to': apply_to}).get_json()


def test_approve_as_local_applies_every_meeting_and_keeps_official(tx, cls):
    before = _q(tx, "SELECT ss.sessionid, ss.endtimeid FROM schedule_sessions ss JOIN schedule_version sv "
                    "ON ss.versionid = sv.versionid AND sv.status = 'Published' WHERE sv.scheduleid = %s "
                    "ORDER BY 1", [cls['scheduleid']])
    res = _approve(tx, cls, 'local')
    assert res['success'], res
    assert {(r['status'], r['applied_to']) for r in _rows(tx)} == {('Approved', 'Local')}
    local = _q(tx, """
        SELECT las.official_sessionid FROM local_arrangement la
        JOIN local_arrangement_sessions las ON las.arrangementid = la.arrangementid
        WHERE la.status = 'Published' AND la.is_active AND la.sectionid = %s
          AND las.official_sessionid = ANY(%s)""",
        [cls['sectionid'], [m['sessionid'] for m in cls['meets']]])
    assert len(local) == 2
    after = _q(tx, "SELECT ss.sessionid, ss.endtimeid FROM schedule_sessions ss JOIN schedule_version sv "
                   "ON ss.versionid = sv.versionid AND sv.status = 'Published' WHERE sv.scheduleid = %s "
                   "ORDER BY 1", [cls['scheduleid']])
    assert after == before                                   # Official untouched


def test_approve_and_update_official_republishes_the_class(tx, cls):
    res = _approve(tx, cls, 'official')
    assert res['success'], res
    assert {(r['status'], r['applied_to']) for r in _rows(tx)} == {('Approved', 'Official')}
    now = _q(tx, """
        SELECT ss.daydesc, ss.endtimeid - ss.starttimeid AS slots
        FROM schedule_sessions ss JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule sc ON sv.scheduleid = sc.scheduleid
        WHERE sv.status = 'Published' AND sc.scheduleid = %s ORDER BY ss.daydesc""", [cls['scheduleid']])
    a, b = cls['meets']
    expected = sorted([(a['daydesc'], a['endtimeid'] - a['starttimeid'] + 1),
                       (b['daydesc'], b['endtimeid'] - b['starttimeid'] - 1)])
    assert sorted((r['daydesc'], r['slots']) for r in now) == expected


def test_rejecting_rejects_the_whole_request(tx, cls):
    assert _submit(cls, _resplit(cls))['success']
    rid = _rows(tx)[-1]['requestid']
    head = _client('Academic Head', cls['head']['username'], cls['head']['employeenumber'])
    res = head.post('/api/requests/decide', json={'type': 'adjustment', 'id': rid,
                                                  'decision': 'Rejected', 'remarks': 'no'}).get_json()
    assert res['success']
    assert {r['status'] for r in _rows(tx)} == {'Rejected'}


def test_a_meeting_with_a_pending_request_cannot_be_requested_again(tx, cls):
    assert _submit(cls, _resplit(cls))['success']
    again = _submit(cls, _resplit(cls), reason='second request')
    assert not again['success'] and 'already has a pending adjustment request' in again['error']


def test_a_meeting_with_an_approved_local_arrangement_cannot_be_requested(tx, cls):
    assert _approve(tx, cls, 'local')['success']
    again = _submit(cls, _resplit(cls), reason='after approval')
    assert not again['success'] and 'already has an approved Local arrangement' in again['error']


def test_a_reason_is_required(tx, cls):
    res = _submit(cls, _resplit(cls), reason='   ')
    assert not res['success'] and 'Please provide a reason' in res['error']


def test_new_meeting_cannot_overlap_the_class_own_unmoved_meeting(tx, cls):
    a, b = cls['meets']
    hb = b['endtimeid'] - b['starttimeid']
    # move only b onto a's day + start time; a stays where it is
    m = [{'official_sessionid': b['sessionid'], 'day': a['daydesc'], 'start_time': a['st'],
          'end_time': SLOTS[SLOTS.index(a['st']) + hb], 'room_id': b['roomid']}]
    res = _submit(cls, m)
    assert not res['success'] and 'not being moved' in res['error']
    assert _rows(tx) == []


def test_a_change_to_the_same_day_time_and_room_is_refused(tx, cls):
    a = cls['meets'][0]
    m = [{'official_sessionid': a['sessionid'], 'day': a['daydesc'], 'start_time': a['st'],
          'end_time': a['en'], 'room_id': a['roomid']}]
    res = _submit(cls, m)
    assert not res['success'] and 'same as the current one' in res['error']


def _validate(cls, rid):
    head = _client('Academic Head', cls['head']['username'], cls['head']['employeenumber'])
    return head.get(f'/api/requests/validate?type=adjustment&id={rid}').get_json()


def _versions(tx, cls):
    return _q(tx, "SELECT COUNT(*) AS n FROM schedule_version WHERE scheduleid = %s", [cls['scheduleid']])[0]['n']


def test_validate_checks_the_official_rules_without_writing_anything(tx, cls):
    assert _submit(cls, _resplit(cls))['success']
    before = _versions(tx, cls)
    v = _validate(cls, _rows(tx)[0]['requestid'])
    if not v.get('all_ok'):
        pytest.skip(f'the re-split conflicts with live data: {v.get("conflicts")}')
    assert v['official_ok'] is True and v['official_error'] is None
    assert _versions(tx, cls) == before                     # dry run: no new version


def test_validate_reports_why_it_cannot_go_into_the_official_schedule(tx, cls, monkeypatch):
    assert _submit(cls, _resplit(cls))['success']
    rid = _rows(tx)[0]['requestid']
    if not _validate(cls, rid).get('all_ok'):
        pytest.skip('the re-split conflicts with live data')
    monkeypatch.setattr(app_module, '_check_designee_night_limit', lambda *a, **k: [
        {'detail': 'Dean X is over the night limit.', 'faculty_id': 'F1'}])
    v = _validate(cls, rid)
    assert v['all_ok'] is True                               # still approvable as Local
    assert v['official_ok'] is False and 'night' in v['official_error']
