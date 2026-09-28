"""
Historical Recommendation Quality (Historical Faculty / Room / Schedule Match).

Regression tests for:
  - bare historical times such as "6:00 - 9:00" being read with the SAME
    normalization Retrieve Previous uses (6:00 PM - 9:00 PM), not a separate
    per-endpoint heuristic that turned the end into 9:00 AM;
  - faculty matched by employee number first ("DEL CASTILLO, Ma. Asuncion R."
    is the same person as "Del Castillo, Maria Asuncion");
  - AY2627 comparing ONLY against AY2526 (never AY2425 or older);
  - the evaluation being a pure function of the CURRENT schedule, so every
    schedule change (Re-generate Selected included) recomputes it — whether
    the change creates, resolves, or has no conflicts.

Fixture data is the real BEED Year 3 / 1st Semester history (AY2526 section
BEED 3; AY2425 differs on purpose for EDUC 014 / EDUC 018 / ELED 314).
Frontend refresh behavior is covered by tests/js/regen_lock_harness.js via
test_retrieve_previous_and_selective_regen.py.
"""
import copy
from datetime import time

import pytest

import app as app_module
from conftest import requires_db

CTX = {'program': 'BEED', 'yearLevel': 3, 'term': 'A', 'acadYear': 'AY2627'}


def test_bare_6_to_9_resolves_to_evening_like_retrieve_previous():
    days, start, end, status, _ = app_module._resolve_historical_day_time('T', '6:00 - 9:00')
    assert (days, start, end, status) == (['Tuesday'], time(18, 0), time(21, 0), 'resolved')
    # Unchanged conventions for the other shapes seen in the data.
    assert app_module._resolve_historical_day_time('W', '9:00 - 12:00')[1:3] == (time(9), time(12))
    assert app_module._resolve_historical_day_time('T', '3:00 - 6:00')[1:3] == (time(15), time(18))
    assert app_module._resolve_historical_day_time('FRI', '1:00-4:00')[1:3] == (time(13), time(16))
    assert app_module._resolve_historical_day_time('M', '7:30 AM - 10:30 AM')[1:3] == (time(7, 30), time(10, 30))


def test_evaluation_has_no_second_historical_time_parser():
    import inspect
    src = inspect.getsource(app_module._compute_schedule_evaluation)
    assert '_resolve_historical_day_time(' in src
    assert '_previous_academic_year(' in src and '_fetch_previous_ay_history(' in src
    assert '_parse_hist_time' not in src


# ── Real-DB ────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def beed3():
    """The retrieved AY2526 BEED 3 schedule — genuinely identical to AY2526."""
    from database import query_db
    sid = query_db("""SELECT s.sectionid FROM sections s
                      JOIN program_yearlevel p ON p.programyearlevelid = s.programyearlevelid
                      WHERE p.programcode = 'BEED' AND p.yearlevel = 3
                        AND p.academicyearid = 'AY2627' LIMIT 1""", one=True)['sectionid']
    body = app_module.app.test_client().post(
        '/api/schedule/retrieve-previous', json=dict(CTX, section=str(sid))).get_json()
    assert body['success'] is True
    return {'section': str(sid), 'rows': body['schedule_data'], 'retrieve_eval': body['evaluation']}


def _hist(ev):
    return ev['categories']['recommendationQuality']['criteria']


def _eval(rows, section, acad_year='AY2627', cross=None):
    return app_module._compute_schedule_evaluation(
        copy.deepcopy(rows), 'BEED', 3, 'A', cross_violations=cross,
        acad_year=acad_year, section_id=section)


def _row(rows, code):
    return next(r for r in rows if r['subject_code'] == code)


def _faculty_id(last, first_prefix):
    from database import query_db
    return query_db("""SELECT employeenumber FROM faculty
                       WHERE UPPER(lastname) = %s AND UPPER(firstname) LIKE %s LIMIT 1""",
                    (last, first_prefix + '%'), one=True)['employeenumber']


@requires_db
def test_identical_to_ay2526_scores_100_on_all_three_historical_metrics(beed3):
    assert _hist(beed3['retrieve_eval']) == {
        'historicalFacultyMatch': 100.0, 'historicalRoomMatch': 100.0, 'historicalScheduleMatch': 100.0}
    assert _hist(_eval(beed3['rows'], beed3['section'])) == _hist(beed3['retrieve_eval'])


@requires_db
def test_6_to_9_historical_time_matches_6pm_to_9pm(beed3):
    rows = [_row(beed3['rows'], 'EDUC 018'), _row(beed3['rows'], 'ELED 314')]
    assert {r['time'] for r in rows} == {'6:00 PM - 9:00 PM'}
    assert _hist(_eval(rows, beed3['section']))['historicalScheduleMatch'] == 100.0


@requires_db
def test_faculty_matched_by_employee_number_despite_different_name_format(beed3):
    row = copy.deepcopy(_row(beed3['rows'], 'EDUC 014'))
    assert row['faculty_id'] == _faculty_id('DEL CASTILLO', 'MA')
    row['instructor'] = 'Del Castillo, Maria Asuncion'   # historical text: "DEL CASTILLO, Ma. Asuncion R."
    assert _hist(_eval([row], beed3['section']))['historicalFacultyMatch'] == 100.0

    # The id decides: same historical-looking name text but a different person.
    other = dict(row, faculty_id=_faculty_id('VALENCIA', 'CHRISTOPHER'),
                 instructor='DEL CASTILLO, Ma. Asuncion R.')
    assert _hist(_eval([other], beed3['section']))['historicalFacultyMatch'] == 0.0


@requires_db
def test_ay2627_compares_only_with_ay2526_never_ay2425(beed3):
    # EDUC 014: AY2526 = WED 9:00-12:00 ; AY2425 = FRI 1:00-4:00.
    row = copy.deepcopy(_row(beed3['rows'], 'EDUC 014'))
    as_2425 = dict(row, start_time='13:00', end_time='16:00', time='1:00 PM - 4:00 PM',
                   days_list=['Friday'], day='Friday', days='FRI')
    assert _hist(_eval([row], beed3['section']))['historicalScheduleMatch'] == 100.0
    assert _hist(_eval([as_2425], beed3['section']))['historicalScheduleMatch'] == 0.0
    # The same rule, one year earlier: AY2526's previous AY is AY2425.
    assert _hist(_eval([as_2425], None, acad_year='AY2526'))['historicalScheduleMatch'] == 100.0


@requires_db
def test_historical_percentages_drop_and_recover_as_values_stop_and_start_matching(beed3):
    rows = beed3['rows']
    changed = copy.deepcopy(rows)
    r = _row(changed, 'EDUC 018')
    r.update(faculty_id=_faculty_id('VALENCIA', 'CHRISTOPHER'), instructor='Valencia, Christopher',
             room='LQ212', start_time='07:30', end_time='10:30', time='7:30 AM - 10:30 AM')
    h = _hist(_eval(changed, beed3['section']))
    assert h == {'historicalFacultyMatch': 87.5, 'historicalRoomMatch': 87.5,
                 'historicalScheduleMatch': 87.5}
    # Identical to AY2526 again -> back to 100.
    assert _hist(_eval(rows, beed3['section']))['historicalFacultyMatch'] == 100.0


def _clean_rows(beed3):
    # ELED 320's recorded 1:00 PM start is not a standard block under the
    # current HC6 grid (a genuine current-AY violation) — leave it out to get a
    # schedule with zero hard violations.
    return [r for r in beed3['rows'] if r['subject_code'] != 'ELED 320']


@requires_db
def test_evaluation_drops_when_a_conflict_is_created_and_recovers_when_resolved(beed3):
    rows = _clean_rows(beed3)
    clean = _eval(rows, beed3['section'])
    assert clean['hardViolationCount'] == 0

    # Put ELED 311 in EDUC 016's room at EDUC 016's time (FRI 7:30-10:30, LQ119).
    clash = copy.deepcopy(rows)
    src, dst = _row(clash, 'EDUC 016'), _row(clash, 'ELED 311')
    dst.update({k: src[k] for k in ('room', 'room_id', 'start_time', 'end_time', 'time',
                                    'days_list', 'day', 'days')})
    bad = _eval(clash, beed3['section'])
    assert bad['hardViolationCount'] > 0 and bad['cspPassed'] is False
    assert bad['categories']['conflictValidation']['criteria']['roomConflictFree'] < 100
    assert bad['overallScore'] < clean['overallScore']

    assert _eval(rows, beed3['section'])['overallScore'] == clean['overallScore']


@requires_db
def test_evaluation_refreshes_with_zero_conflicts(beed3):
    rows = _clean_rows(beed3)
    before = _eval(rows, beed3['section'])
    moved = copy.deepcopy(rows)
    _row(moved, 'ELED 311').update(start_time='16:30', end_time='19:30', time='4:30 PM - 7:30 PM')
    after = _eval(moved, beed3['section'])
    assert before['hardViolationCount'] == after['hardViolationCount'] == 0
    assert _hist(before)['historicalScheduleMatch'] == 100.0
    assert _hist(after)['historicalScheduleMatch'] == round(100 * 6 / 7, 1)  # 6 of 7 still match


@requires_db
def test_accuracy_endpoint_matches_retrieve_evaluation_including_cross_section_conflicts(beed3):
    body = app_module.app.test_client().post('/api/schedule/accuracy', json={
        'schedule_data': beed3['rows'], 'program': 'BEED', 'year_level': 3, 'term': 'A',
        'acad_year': 'AY2627', 'section': beed3['section']}).get_json()
    for k in ('overallScore', 'hardViolationCount', 'cspPassed'):
        assert body[k] == beed3['retrieve_eval'][k], k
    assert _hist(body) == _hist(beed3['retrieve_eval'])


_PLUCK = ['subject_code', 'class_type', 'course', 'description', 'lec_hours', 'lab_hours', 'units',
          'faculty_id', 'instructor', 'room_id', 'room', 'room_type', 'days_list', 'day', 'time', 'days']
_UNLOCKED = {'faculty': False, 'room': False, 'schedule': False}
_LOCKED = {'faculty': True, 'room': True, 'schedule': True}


def _regenerate(beed3, code):
    payload = [dict({k: r[k] for k in _PLUCK if k in r}, row_key=r['subject_code'],
                    selected=r['subject_code'] == code,
                    lock=_UNLOCKED if r['subject_code'] == code else _LOCKED)
               for r in beed3['rows']]
    return app_module.app.test_client().post('/api/schedule/generate', json=dict(
        CTX, curriculum='2022-2023', section=beed3['section'], locked_sessions=payload)).get_json()


@requires_db
def test_regenerate_selected_returns_evaluation_of_the_new_schedule(beed3):
    """The evaluation in a Re-generate Selected response is computed from the
    regenerated schedule itself (never carried over from before the change).
    The unselected rows' existing faculty-load (HC9) and cross-section
    conflicts no longer reject the whole regeneration."""
    res = _regenerate(beed3, 'ELED 318')
    assert res['success'] is True, res.get('error')
    new = next(g for g in res['schedule_data'] if g['subject_code'] == 'ELED 318')
    fresh = app_module.app.test_client().post('/api/schedule/accuracy', json={
        'schedule_data': res['schedule_data'], 'program': 'BEED', 'year_level': 3, 'term': 'A',
        'acad_year': 'AY2627', 'section': beed3['section']}).get_json()
    ev = res['evaluation']
    for k in ('overallScore', 'hardViolationCount', 'cspPassed'):
        assert ev[k] == fresh[k], k
    assert _hist(ev) == _hist(fresh)
    # ELED 318's AY2526 value is SAT 7:30-10:30 AM, room TBA; if the regenerated
    # time/room no longer match, the historical percentages must drop.
    old = _row(beed3['rows'], 'ELED 318')
    if (new['days'], new['room']) != (old['days'], old['room']) or '7:30' not in new['time']:
        assert _hist(ev)['historicalScheduleMatch'] < 100.0


@requires_db
def test_regenerating_the_conflicting_row_resolves_its_violation(beed3):
    before = beed3['retrieve_eval']['hardViolationCount']
    res = _regenerate(beed3, 'ELED 320')          # recorded at non-standard 1:00 PM (HC6)
    assert res['success'] is True, res.get('error')
    ev = res['evaluation']
    assert not any('ELED 320' in (v.get('detail') or '') and v.get('rule') == 'HC6'
                   for vs in ev['violationsBySubject'].values() for v in vs)
    # Like-for-like count: HC9 (faculty load) is excluded because the two
    # evaluations cannot measure it the same way — retrieved historical rows
    # carry no duration, so the Retrieve evaluation never counts their hours,
    # while the regenerated rows do, together with the hours each faculty
    # already teaches in other sections (Fix #6: the same HC9 inputs the solver
    # and the Approve gate use).
    before_non_load = before - sum(1 for c in beed3['retrieve_eval']['conflicts'] if c['rule'] == 'HC9')
    after_non_load = ev['hardViolationCount'] - sum(1 for c in ev['conflicts'] if c['rule'] == 'HC9')
    assert after_non_load < before_non_load
