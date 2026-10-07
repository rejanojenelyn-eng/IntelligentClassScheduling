"""Phase 9: every live reader uses the SAME effective schedule.

Effective = Official Published
            MINUS Official occurrences overridden by an active Local Published
            PLUS  the active Local Published sessions.
Drafts (Official or Local) are never live.

One scenario, built inside a rolled-back transaction (tests/_shared_tx.py):
  subject A Published Mon + Thu 07:30-09:00 (faculty FX, room R0)
  pending Official Draft of A: Tue 10:00          -> not live
  active Local Published: Thu occurrence -> Sat    -> Thu hidden, Sat live
  inactive Local Draft:   Mon occurrence -> Fri    -> not live
=> live for FX / section / subject A: {Monday 07:30, Saturday 07:30}
"""
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import faculty_load
from conftest import requires_db
from test_official_persistence_repair import (  # noqa: F401  (fixtures)
    tx, scope, _row, _seed, _cur)

LIVE = {('Monday', '07:30'), ('Saturday', '07:30')}


@pytest.fixture
def fx(tx, scope):
    """A real faculty member with no live sessions this semester."""
    cur = _cur(tx)
    cur.execute("WITH " + faculty_load.EFFECTIVE_SESSIONS_CTE + """
        SELECT f.employeenumber FROM faculty f
        WHERE f.isactive AND NOT EXISTS (
            SELECT 1 FROM effective_sessions es
            WHERE es.employeenumber = f.employeenumber AND es.semesterid = %s)
          AND NOT EXISTS (
            SELECT 1 FROM schedule_version sv JOIN schedule s ON sv.scheduleid = s.scheduleid
            WHERE s.semesterid = %s AND COALESCE(sv.employeenumber, s.employeenumber) = f.employeenumber
              AND sv.status IN ('Draft', 'Published'))
        ORDER BY f.employeenumber LIMIT 1
    """, (scope['semesterid'], scope['semesterid']))
    row = cur.fetchone()
    if not row:
        pytest.skip('no idle faculty member this semester')
    return row['employeenumber']


def _occ(tx, scope, code, day, status):
    cur = _cur(tx)
    cur.execute("""
        SELECT ss.sessionid, sv.versionid FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE s.sectionid = %s AND s.semesterid = %s AND UPPER(cs.subjectcode) = %s
          AND ss.daydesc = %s AND sv.status = %s
    """, (scope['sectionid'], scope['semesterid'], code, day, status))
    return cur.fetchone()


def _local(tx, scope, occ, day, start_id, end_id, room, fac, status):
    cur = _cur(tx)
    cur.execute("""
        INSERT INTO local_arrangement (programcode, yearlevel, sectionid, semesterid, ref_versionid,
                                       is_active, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW()) RETURNING arrangementid
    """, (scope['program'], scope['yearlevel'], scope['sectionid'], scope['semesterid'],
          occ['versionid'], status == 'Published', status))
    arr = cur.fetchone()['arrangementid']
    cur.execute("""
        INSERT INTO local_arrangement_sessions (arrangementid, subjectcode, daydesc, starttimeid,
                                                endtimeid, roomid, faculty_employeenumber, official_sessionid)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (arr, scope['A'], day, start_id, end_id, room, fac, occ['sessionid']))


def _tid(tx, hhmm):
    cur = _cur(tx)
    cur.execute("SELECT timeid FROM timeslot WHERE LEFT(timevalue::text, 5) = %s", (hhmm,))
    return cur.fetchone()['timeid']


@pytest.fixture
def world(tx, scope, fx):
    A = scope['A']
    row = lambda day, st, et, **k: dict(_row(scope, A, day, st, et, **k), employeenumber=fx, faculty_id=fx)
    _seed(tx, scope, 'Published', 1, [row('Monday', '07:30', '09:00'), row('Thursday', '07:30', '09:00')])
    _seed(tx, scope, 'Draft', 2, [row('Tuesday', '10:00', '11:30')])
    thu = _occ(tx, scope, A, 'Thursday', 'Published')
    mon = _occ(tx, scope, A, 'Monday', 'Published')
    s730, e900 = _tid(tx, '07:30'), _tid(tx, '09:00')
    _local(tx, scope, thu, 'Saturday', s730, e900, scope['rooms'][1], fx, 'Published')
    _local(tx, scope, mon, 'Friday', s730, e900, scope['rooms'][1], fx, 'Draft')
    tx.handle().commit()
    return {'fx': fx, 'A': A}


def _client(tx, fac):
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive ORDER BY userid LIMIT 1")
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['loggedin'] = True; s['role'] = 'Faculty'
        s['username'] = cur.fetchone()['username']; s['employeenumber'] = fac
    return c


def _hhmm(t):
    from datetime import datetime
    return datetime.strptime(t.strip(), '%I:%M %p').strftime('%H:%M')


@requires_db
def test_faculty_load_reader_is_live(tx, scope, world):
    sessions = faculty_load.get_faculty_sessions(_cur(tx), world['fx'], scope['academicyearid'],
                                                 scope['semestertype'])
    got = {(s['days'], _hhmm(s['time_range'].split(' - ')[0])) for s in sessions
           if s['sectionid'] == scope['sectionid']}
    assert got == LIVE


@requires_db
def test_my_assignments_reader_is_live(tx, scope, world):
    rows = _client(tx, world['fx']).get('/api/faculty/my_assignments').get_json()['assignments']
    got = {(r['daydesc'], _hhmm(r['start_time'])) for r in rows if r['sectionid'] == scope['sectionid']}
    assert got == LIVE
    sat = next(r for r in rows if r['daydesc'] == 'Saturday')
    thu = _occ(tx, scope, world['A'], 'Thursday', 'Published')
    assert sat['source'] == 'Local' and sat['official_sessionid'] == thu['sessionid']
    assert sat['versionid'] == thu['versionid']          # requests bind to the Official occurrence


@requires_db
@pytest.mark.parametrize('day, blocked', [('Monday', True), ('Saturday', True), ('Thursday', False),
                                          ('Tuesday', False), ('Friday', False)])
def test_blocked_times_reader_is_live(tx, scope, world, day, blocked):
    body = _client(tx, world['fx']).get(f'/api/faculty/blocked_times?day={day}').get_json()
    starts = {_hhmm(b['start_time']) for b in body['blocked']}
    assert ('07:30' in starts) is blocked
    assert '10:00' not in starts                         # the pending Draft never blocks


@requires_db
def test_room_report_reader_is_live(tx, scope, world):
    rows = app_module._room_report_fetch(_cur(tx), [scope['academicyearid']], [scope['semestertype']],
                                         None, None, None)
    got = {(r['Day'], _hhmm(r['Time'].split(' - ')[0])) for r in rows
           if str(r['SubjectCode']).upper() == world['A'] and r['Instructor'] != 'TBA'
           and (r['OfficialSessionID'] in {o['sessionid'] for o in [
               _occ(tx, scope, world['A'], 'Monday', 'Published'),
               _occ(tx, scope, world['A'], 'Thursday', 'Published')]})}
    assert got == LIVE


@requires_db
def test_all_live_readers_agree(tx, scope, world):
    cur = _cur(tx)
    load = {(s['days'], _hhmm(s['time_range'].split(' - ')[0]))
            for s in faculty_load.get_faculty_sessions(cur, world['fx'], scope['academicyearid'],
                                                       scope['semestertype'])
            if s['sectionid'] == scope['sectionid']}
    assign = {(r['daydesc'], _hhmm(r['start_time']))
              for r in _client(tx, world['fx']).get('/api/faculty/my_assignments').get_json()['assignments']
              if r['sectionid'] == scope['sectionid']}
    hours = faculty_load.get_faculty_hours_batch(cur, scope['academicyearid'], scope['semestertype'])
    assert load == assign == LIVE
    assert float(faculty_load.load_total(hours.get(world['fx']))) == 3.0   # two 1.5h live meetings


# ── 9.4 HC8: live schedule + candidate replacing what it stands for ────────────

def _designee(allowance):
    return {'designationid': 1, 'nightteachingservice': allowance, 'fullname': 'Test Designee'}


def _night(scope, code, day, fac):
    return dict(_row(scope, code, day, '18:00', '19:30'), employeenumber=fac, faculty_id=fac)


def _hc8(scope, fac, candidate, allowance, replaced):
    from datetime import time
    for c in candidate:
        c['start_time'], c['end_time'] = time(18, 0), time(19, 30)
    return app_module._check_designee_night_limit(
        candidate, {fac: _designee(allowance)}, scope['semesterid'],
        exclude_program=scope['program'], exclude_year_level=scope['yearlevel'],
        exclude_section_id=scope['sectionid'], replaced_codes=replaced)


@requires_db
@pytest.mark.parametrize('allowance, violates', [(1, True), (2, False)])
def test_hc8_published_only(tx, scope, fx, allowance, violates):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_night(scope, A, 'Monday', fx)])
    v = _hc8(scope, fx, [_night(scope, B, 'Tuesday', fx)], allowance, [B])
    assert bool(v) is violates


@requires_db
def test_hc8_published_plus_pending_draft_is_not_double_counted(tx, scope, fx):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_night(scope, A, 'Monday', fx)])
    _seed(tx, scope, 'Draft', 2, [_night(scope, A, 'Wednesday', fx)])     # pending, not live
    assert _hc8(scope, fx, [_night(scope, B, 'Tuesday', fx)], 2, [B]) == []


@requires_db
def test_hc8_published_overridden_by_local_counts_the_local_night(tx, scope, fx):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_night(scope, A, 'Monday', fx)])
    mon = _occ(tx, scope, A, 'Monday', 'Published')
    _local(tx, scope, mon, 'Thursday', _tid(tx, '18:00'), _tid(tx, '19:30'), scope['rooms'][0], fx, 'Published')
    tx.handle().commit()
    assert _hc8(scope, fx, [_night(scope, B, 'Tuesday', fx)], 2, [B]) == []          # Thu + Tue
    v = _hc8(scope, fx, [_night(scope, B, 'Tuesday', fx)], 1, [B])
    assert v and v[0]['existing_nights'] == ['Thursday']                            # not Monday


@requires_db
def test_hc8_candidate_replaces_the_published_subject(tx, scope, fx):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_night(scope, A, 'Monday', fx)])
    # Publishing A moved to Tuesday: the live Monday occurrence is being replaced.
    assert _hc8(scope, fx, [_night(scope, A, 'Tuesday', fx)], 1, [A]) == []


@requires_db
@pytest.mark.parametrize('allowance, violates', [(3, False), (2, True)])
def test_hc8_multiple_legitimate_night_assignments(tx, scope, fx, allowance, violates):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_night(scope, A, 'Monday', fx), _night(scope, B, 'Wednesday', fx)])
    v = _hc8(scope, fx, [_night(scope, 'ZZZ 999', 'Friday', fx)], allowance, ['ZZZ 999'])
    assert bool(v) is violates
    if v:
        assert v[0]['night_days'] == ['Monday', 'Wednesday', 'Friday']
