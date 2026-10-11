"""Generate Schedule never double-books a room or a faculty member already used by another
section's Draft or Published class (checked independently of the generator's own
conflict check, against the real database; nothing is saved).

A same-subject overlap where the faculty matches or is TBA on one side is an allowed
merge (P7 / NSTP), not a double booking."""
import pytest

import app as app_module
from conftest import requires_db
from database import query_db

pytestmark = requires_db

SECTIONS = [('BSIT', 1, 525), ('BSCE', 2, 506), ('DCVET', 3, 92), ('BSHM', 2, 521)]


def _mins(v):
    if v is None:
        return None
    if hasattr(v, 'hour'):
        return v.hour * 60 + v.minute
    h, m = str(v).split(':')[:2]
    return int(h) * 60 + int(m)


@pytest.fixture(scope='module')
def client():
    acct = query_db("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' "
                    "ORDER BY userid LIMIT 1", one=True)
    if not acct:
        pytest.skip('no active Academic Head account')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=acct['username'])
    return c


@pytest.fixture(scope='module')
def occupancy():
    return query_db("""
        SELECT s.sectionid, UPPER(cs.subjectcode) AS code, ss.daydesc AS day, ss.roomid,
               COALESCE(sv.employeenumber, s.employeenumber) AS fac, t1.timevalue AS st, t2.timevalue AS et
        FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid
        JOIN schedule s ON s.scheduleid = sv.scheduleid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = s.curriculumsubjectid
        JOIN semester sem ON sem.semesterid = s.semesterid
        JOIN timeslot t1 ON t1.timeid = ss.starttimeid
        JOIN timeslot t2 ON t2.timeid = ss.endtimeid
        WHERE sem.academicyearid = 'AY2627' AND sem.semestertype = 'A'
          AND sv.status IN ('Published', 'Draft') AND ss.daydesc IS NOT NULL""") or []


@pytest.mark.parametrize('prog,yl,sid', SECTIONS)
def test_generated_classes_do_not_clash_with_drafts_or_published(client, occupancy, prog, yl, sid):
    if not occupancy:
        pytest.skip('no Draft/Published schedules for AY2627 1st semester')
    cy = client.get(f'/api/get_curriculum?program={prog}&year_level={yl}&ay_id=AY2627').get_json().get('curriculum_year')
    if not cy:
        pytest.skip(f'no curriculum for {prog} {yl}')
    d = client.post('/api/schedule/generate', json={'program': prog, 'yearLevel': yl, 'term': 'A',
                                                     'curriculum': cy, 'section': sid, 'acadYear': 'AY2627',
                                                     'locked_sessions': []}).get_json()
    rows = d.get('schedule_data') or []
    if not rows:
        pytest.skip(f'nothing generated for {prog} {yl}: {d.get("error")}')
    codes = {(g.get('subject_code') or '').upper() for g in rows}
    # This section's own rows of the generated subjects are what the result replaces.
    others = [r for r in occupancy if not (str(r['sectionid']) == str(sid) and r['code'] in codes)]
    clashes = []
    for g in rows:
        if not g.get('start_time') or not g.get('days_list'):
            continue
        gs, ge = _mins(g['start_time']), _mins(g['end_time'])
        code = (g.get('subject_code') or '').upper()
        for r in others:
            if r['day'] not in g['days_list'] or not (gs < _mins(r['et']) and _mins(r['st']) < ge):
                continue
            same_room = bool(g.get('room_id')) and r['roomid'] is not None and str(g['room_id']) == str(r['roomid'])
            same_fac = bool(g.get('faculty_id')) and bool(r['fac']) and str(g['faculty_id']) == str(r['fac'])
            if not (same_room or same_fac):
                continue
            allowed_merge = r['code'] == code and (same_fac or not g.get('faculty_id') or not r['fac'])
            if not allowed_merge:
                clashes.append((code, r['day'], g.get('time'), 'room' if same_room else 'faculty', r['code']))
    assert clashes == []
