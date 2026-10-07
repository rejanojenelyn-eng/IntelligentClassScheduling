"""An approved make-up class holds its room / faculty / section on its one date.

A weekly change onto that weekday + time (Local Scheduler save/publish, or the
Academic Head approving a faculty's Schedule Adjustment) must wait until the
make-up date has passed. The faculty may still SUBMIT the adjustment request.
"""
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from app import _local_upcoming_makeup_block, _makeup_wait_message, get_db_connection

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / 'app.py').read_text(encoding='utf-8')


def _route_body(route):
    start = APP_SRC.index(f"@app.route('{route}'")
    nxt = APP_SRC.find('\n@app.route(', start + 1)
    return APP_SRC[start:nxt if nxt > 0 else len(APP_SRC)]


def test_local_save_publish_and_live_check_all_run_the_guard():
    for route in ('/api/local/save_arrangement',
                  '/api/local/arrangement/<int:arr_id>/publish',
                  '/api/local/check_room_conflicts'):
        body = _route_body(route)
        assert '_local_upcoming_makeup_block(' in body, route
        assert "'UPCOMING_MAKEUP_CLASS'" in body, route


def test_adjustment_approval_names_the_pending_makeup():
    fn = APP_SRC[APP_SRC.index('def _request_conflict_summary'):]
    fn = fn[:fn.index('\n@app.route(')]
    assert 'from_date=today' in fn
    assert "makeup_waits['room']" in fn and "_makeup_wait_message(makeup_waits['room']" in fn


def test_faculty_adjustment_check_reports_notice_without_blocking():
    body = _route_body('/api/faculty/check_request_conflicts')
    assert "'makeup_notice':" in body
    # the notice is informational: it never flips the *_conflict flags
    notice_block = body[body.index('makeup_notice = '):body.index('# Duration check')]
    assert '_conflict = True' not in notice_block


def test_wait_message_wording():
    msg = _makeup_wait_message({'subjectcode': 'COMP 018', 'start_t': '02:00 PM', 'end_t': '04:00 PM',
                                'requested_date': date(2026, 10, 12)}, 'room', 'This adjustment')
    assert msg == ('The room has an approved make-up class (COMP 018, 02:00 PM–04:00 PM) on '
                   'October 12, 2026. This adjustment can only be approved after that date.')


@pytest.fixture
def tx():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        conn.rollback(); cur.close(); conn.close()


def test_guard_blocks_until_the_makeup_date_has_passed(tx):
    cur = tx
    cur.execute("""
        SELECT sc.scheduleid, sc.semesterid, sc.sectionid, sc.employeenumber,
               UPPER(cs.subjectcode) AS subj
        FROM schedule sc JOIN semester s ON s.semesterid = sc.semesterid AND s.isactive
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        WHERE sc.sectionid IS NOT NULL AND sc.employeenumber IS NOT NULL LIMIT 1""")
    cls = cur.fetchone()
    cur.execute("SELECT roomid FROM room ORDER BY roomid LIMIT 2")
    rooms = [r['roomid'] for r in cur.fetchall()]
    cur.execute("SELECT timeid, TO_CHAR(timevalue,'HH24:MI') t FROM timeslot ORDER BY timevalue")
    ts = {r['t']: r['timeid'] for r in cur.fetchall()}
    if not cls or len(rooms) < 2 or not {'14:00', '16:00'} <= ts.keys():
        pytest.skip('needs an active-semester class, two rooms and 2:00/4:00 PM timeslots')

    today = date.today()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='class_meeting_request'")
    cols = {r['column_name'] for r in cur.fetchall()}
    vals = {'scheduleid': cls['scheduleid'], 'requested_date': monday, 'new_starttimeid': ts['14:00'],
            'new_endtimeid': ts['16:00'], 'new_roomid': rooms[0], 'status': 'Approved'}
    for col, val in (('submitted_by', cls['employeenumber']), ('reason', 'test'),
                     ('reviewed_by', cls['employeenumber']), ('reviewed_at', today)):
        if col in cols:
            vals[col] = val
    cur.execute(f"INSERT INTO class_meeting_request ({','.join(vals)}) "
                f"VALUES ({','.join(['%s'] * len(vals))})", list(vals.values()))

    base = {'subjectcode': 'ZZZ999', 'daydesc': 'Monday',
            'starttimeid': ts['14:00'], 'endtimeid': ts['16:00']}

    def chk(sec=-1, today_=None, **s):
        return _local_upcoming_makeup_block(cur, [dict(base, **s)], cls['semesterid'], sec,
                                            **({'today': today_} if today_ else {}))

    assert 'The room has an approved make-up' in chk(roomid=rooms[0], faculty='NOBODY')
    assert 'The faculty has an approved make-up' in chk(roomid=rooms[1], faculty=cls['employeenumber'])
    assert 'The section has an approved make-up' in chk(sec=cls['sectionid'], roomid=rooms[1], faculty='NOBODY')
    assert chk(roomid=rooms[1], faculty='NOBODY') is None                          # nothing shared
    assert chk(roomid=rooms[0], faculty='NOBODY', daydesc='Tuesday') is None       # other weekday
    assert chk(roomid=rooms[0], faculty='NOBODY', today_=monday) is not None       # make-up day itself
    assert chk(roomid=rooms[0], faculty='NOBODY', today_=monday + timedelta(days=1)) is None  # date passed
    assert chk(sec=cls['sectionid'], roomid=rooms[1], faculty='NOBODY',
               subjectcode=cls['subj']) is None                                     # its own make-up
