"""Bugs found by the browser smoke tests (Phase 11) — locked in against the real database.

1. The HC15 cross-schedule check selected sec.sectionname without joining `sections`, so every
   real Publish failed closed with HTTP 503 ("a required constraint check could not be
   completed"). Publish tests stub that check, which hid it.
2. /api/manual/faculty_schedule returned raw datetime.time values -> HTTP 500 (not JSON
   serializable) for faculty with Local rows.
"""
import pytest

import app as app_module
from conftest import requires_db
from test_local_override_snapshot_repair import tx, ls, client, _cur  # noqa: F401  (fixtures)


@requires_db
def test_hc15_cross_schedule_check_runs_its_real_query(tx, ls):
    o = ls['occ'][0]
    cur = _cur(tx)
    cur.execute("SELECT LEFT(timevalue::text, 5) AS t FROM timeslot WHERE timeid IN (%s, %s) ORDER BY timeid",
                (o['starttimeid'], o['endtimeid']))
    st, et = [r['t'] for r in cur.fetchall()]
    candidate = [{'subject_code': o['code'], 'subjectcode': o['code'], 'day': o['daydesc'],
                  'days_list': [o['daydesc']], 'start_time': st, 'end_time': et,
                  'room_id': o['roomid'], 'faculty_id': o['fac']}]
    candidate = app_module._rehydrate_schedule(candidate)
    # fail_closed=True: any SQL error raises ConstraintCheckUnavailable instead of returning.
    violations, count = app_module._check_cross_schedule_conflicts(
        candidate, ls['program'], ls['yearlevel'], ls['semestertype'], ls['academicyearid'],
        section_id=ls['sectionid'], fail_closed=True)
    assert isinstance(violations, list) and count == len(violations)


@requires_db
def test_faculty_schedule_endpoint_serializes_local_rows(tx, ls, client):
    cur = _cur(tx)
    cur.execute("""
        SELECT las.faculty_employeenumber AS fac FROM local_arrangement la
        JOIN local_arrangement_sessions las USING (arrangementid)
        WHERE la.status = 'Published' AND la.is_active AND las.faculty_employeenumber IS NOT NULL LIMIT 1
    """)
    row = cur.fetchone()
    fac = row['fac'] if row else ls['occ'][0]['fac']
    for mode in ('official', 'local'):
        r = client.get(f"/api/manual/faculty_schedule?emp_num={fac}&ay_id={ls['academicyearid']}"
                       f"&semester={ls['semestertype']}&scheduler_mode={mode}")
        assert r.status_code == 200, (mode, r.get_data(as_text=True)[:200])
        assert isinstance(r.get_json(), list)
