"""
One source of truth for a selected row's regeneration locks.

tests/js/selection_state_harness.js loads a real Retrieve Previous / Generate
response into the REAL page script and records the lock state every row gets
through each selection path (row checkbox, Select Conflict Rows, Select
Incomplete, Select All), a bulk selection over an already-selected row with a
USER lock, and an attempt to lock an unresolved value.

  components_to_regenerate = conflict components ∪ incomplete components
  -> unlocked; every other component is AUTO-kept.

Live data: BEED 4 / AY2526 (EDUC 021 TBA; EDUC 022 TBA + time block + faculty
clash; EDUC 023 TBA + faculty clash). Synthetic responses cover day-only,
time-only, day+time and naming variants, and a nonexistent room.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import app as app_module
from conftest import requires_db

HARNESS = Path(__file__).resolve().parent / 'js' / 'selection_state_harness.js'
L, U = 'is-locked', 'is-unlocked'


def _locks(faculty, schedule, room):
    return {'faculty': faculty, 'schedule': schedule, 'room': room, 'icons': 3}


def _run(response, tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    f = tmp_path / 'response.json'
    f.write_text(json.dumps(response), encoding='utf-8')
    out = subprocess.run([node, str(HARNESS), str(f)], capture_output=True, text=True,
                         encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── Live BEED 4 data ────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def beed4(tmp_path_factory):
    body = app_module.app.test_client().post('/api/schedule/retrieve-previous', json={
        'program': 'BEED', 'yearLevel': 4, 'term': 'A', 'acadYear': 'AY2627',
        'section': '12', 'curriculum': '2022-2023'}).get_json()
    assert body['success'] is True
    return body, _run(body, tmp_path_factory.mktemp('beed4'))


EXPECTED = {
    'EDUC 021': _locks(U, U, U),   # Room TBA only -> incomplete rows start all unlocked
    'EDUC 022': _locks(U, U, U),   # TBA + time block + faculty clash
    'EDUC 023': _locks(U, U, U),   # TBA + faculty clash (instructor, day, time)
}


@requires_db
def test_backend_components_for_educ_rows(beed4):
    body, _ = beed4
    ev = body['evaluation']
    hc10_023 = [c for c in ev['conflicts'] if c['rule'] == 'HC10'
                and c.get('targets') and c['targets'][0]['subject_code'] == 'EDUC 023']
    # An active Published Local override may legitimately remove the old Official
    # HC10 clash. If HC10 remains effective, its component metadata must be exact.
    if hc10_023:
        assert hc10_023[0]['affected_components'] == ['instructor', 'day', 'time']
        assert hc10_023[0]['resolution_components'] == ['faculty', 'schedule']
    assert {e['subject_code']: e['components'] for e in ev['incomplete']} == \
        {'EDUC 021': ['room'], 'EDUC 022': ['room'], 'EDUC 023': ['room']}


@requires_db
def test_manual_selection_of_tba_row_opens_room(beed4):
    assert beed4[1]['manual']['EDUC 021'] == EXPECTED['EDUC 021']


@requires_db
def test_select_conflict_rows_opens_conflict_and_incomplete_components(beed4):
    s = beed4[1]['selectConflictRows']
    body = beed4[0]
    targeted = {t['subject_code'] for c in body['evaluation']['conflicts'] for t in c.get('targets', [])}
    # Select Conflict Rows must follow the CURRENT effective conflict list. A row
    # whose old Official clash was displaced by Published Local must not be selected.
    for code in ('EDUC 021', 'EDUC 022', 'EDUC 023'):
        if code in targeted:
            assert s[code]['icons'] == 3
        else:
            assert s[code]['icons'] == 0


@requires_db
def test_select_incomplete_opens_incomplete_and_conflict_components(beed4):
    assert beed4[1]['selectIncomplete'] == EXPECTED


@requires_db
def test_every_selection_path_gives_the_same_state_for_the_same_row(beed4):
    r = beed4[1]
    for code in EXPECTED:
        states = [r['manual'][code], r['selectIncomplete'][code], r['selectAll'][code]]
        if r['selectConflictRows'][code]['icons']:
            states.append(r['selectConflictRows'][code])
        assert all(st == EXPECTED[code] for st in states), (code, states)


@requires_db
def test_bulk_selection_reconciles_an_already_selected_row_and_keeps_its_user_lock(beed4):
    r = beed4[1]
    assert r['preBulk']['code'] == 'EDUC 023' and r['preBulk']['userField'] == 'faculty'
    assert r['preBulk']['origin'] == 'user' and r['preBulk']['state']['faculty'] == L
    for key in ('afterBulkOnSelected', 'afterSecondBulkOnSelected'):
        assert r[key]['origin'] == 'user'
        assert r[key]['state'] == _locks(L, U, U)     # user lock kept; auto states recomputed


@requires_db
def test_tba_room_cannot_become_user_locked(beed4):
    assert beed4[1]['lockUnresolved'] == {
        code: {'room': U, 'origin': 'unresolved'} for code in ('EDUC 021', 'EDUC 022', 'EDUC 023')}


# ── Synthetic responses: component-name normalization, nonexistent room ─────

def _row(code, **over):
    r = {'subject_code': code, 'faculty_id': 'F1', 'instructor': 'Doe, Jane', 'class_type': 'Lecture',
         'course': 'BEED', 'room_id': 18, 'room': 'LQ117', 'time': '7:30 AM - 10:30 AM',
         'days': 'MON', 'days_list': ['Monday']}
    r.update(over)
    return r


def _response(rows, conflicts=(), incomplete=()):
    ev = {'success': True, 'overallScore': 80, 'cspPassed': not conflicts,
          'hardViolationCount': len(conflicts), 'eligibleForApproval': False, 'completionRate': 100,
          'incompleteCount': 0, 'categories': {}, 'violationsBySubject': {},
          'conflicts': list(conflicts), 'incomplete': list(incomplete)}
    return {'success': True, 'schedule_data': rows, 'retrieved_from': {'ay_label': 'AY202526'},
            'conflict_count': len(conflicts), 'evaluation': ev}


def _conflict(code, affected, resolution=()):
    return {'id': 1, 'rule': 'HCX', 'type': 'Conflict', 'detail': 'x', 'subject': code, 'source': 'schedule',
            'affected_components': list(affected), 'resolution_components': list(resolution),
            'targets': [{'subject_code': code, 'faculty_id': None}]}


@pytest.mark.parametrize('affected', [['day'], ['time'], ['day', 'time'], ['days'], ['time_days'],
                                      ['day_time'], ['time_day'], ['DAY', 'Time']])
def test_day_and_or_time_conflict_opens_the_single_time_days_lock(tmp_path, affected):
    rows = [_row('A'), _row('B', time='1:00 PM - 4:00 PM')]
    r = _run(_response(rows, conflicts=[_conflict('A', affected)]), tmp_path)
    for path in ('manual', 'selectConflictRows', 'selectAll'):
        assert r[path]['A'] == _locks(L, U, L), path   # one Time/Days lock, unlocked; rest auto-kept
    assert r['manual']['B'] == _locks(U, U, U)         # no problem: whole row regenerable


def test_conflict_with_only_resolution_components_is_still_honoured(tmp_path):
    rows = [_row('A')]
    r = _run(_response(rows, conflicts=[_conflict('A', [], ['room'])]), tmp_path)
    assert r['selectConflictRows']['A'] == _locks(L, L, U)


@pytest.mark.parametrize('room_over', [{'room_id': None, 'room': 'TBA'},
                                       {'room_id': 99999, 'room': 'LQ999'},
                                       {'room_id': None, 'room': ''}])
def test_nonexistent_or_blank_room_behaves_like_tba(tmp_path, room_over):
    rows = [_row('A', **room_over)]
    inc = [{'subject_code': 'A', 'components': ['room'], 'reasons': ['x'],
            'targets': [{'subject_code': 'A', 'faculty_id': 'F1'}]}]
    r = _run(_response(rows, incomplete=inc), tmp_path)
    assert r['manual']['A'] == _locks(U, U, U)          # incomplete row: all unlocked
    assert r['selectIncomplete']['A'] == _locks(U, U, U)
    assert r['lockUnresolved']['A'] == {'room': U, 'origin': 'unresolved'}


def test_select_conflict_rows_and_select_incomplete_agree_on_a_row_with_both(tmp_path):
    rows = [_row('A', room_id=None, room='TBA')]
    inc = [{'subject_code': 'A', 'components': ['room'], 'reasons': ['x'],
            'targets': [{'subject_code': 'A', 'faculty_id': 'F1'}]}]
    r = _run(_response(rows, conflicts=[_conflict('A', ['instructor'])], incomplete=inc), tmp_path)
    assert r['selectConflictRows']['A'] == r['selectIncomplete']['A'] == r['manual']['A'] == _locks(U, U, U)


# ── Automatic selection of incomplete rows on display ───────────────────────

_NO = {'faculty': None, 'schedule': None, 'room': None, 'icons': 0}


@requires_db
def test_three_tba_rows_are_automatically_selected_and_prepared(beed4):
    r = beed4[1]
    assert r['initial'] == EXPECTED                     # all three checked, TBA Room unlocked
    assert r['initialGenerateCalls'] == 0               # selection never regenerates


@requires_db
def test_manually_unchecked_rows_stay_unchecked_until_select_incomplete(beed4):
    r = beed4[1]
    assert r['afterManualUnchecks'] == {code: _NO for code in EXPECTED}
    assert r['selectIncomplete'] == EXPECTED            # the explicit button reselects them


def _inc(code, comps, fid='F1'):
    return {'subject_code': code, 'components': comps, 'reasons': ['x'],
            'targets': [{'subject_code': code, 'faculty_id': fid}]}


# A newly selected INCOMPLETE row starts with Instructor, Time/Days and Room all
# unlocked, whatever else is wrong with it (the Academic Head locks what to keep).
@pytest.mark.parametrize('rows,conflicts,incomplete,expected', [
    # TBA Room only
    ([_row('A', room_id=None, room='TBA')], [], [_inc('A', ['room'])], _locks(U, U, U)),
    # TBA + faculty conflict
    ([_row('A', room_id=None, room='TBA')], [_conflict('A', ['instructor'])], [_inc('A', ['room'])], _locks(U, U, U)),
    # TBA + time conflict
    ([_row('A', room_id=None, room='TBA')], [_conflict('A', ['time'])], [_inc('A', ['room'])], _locks(U, U, U)),
    # TBA + faculty + time conflict -> all three
    ([_row('A', room_id=None, room='TBA')], [_conflict('A', ['instructor', 'day', 'time'])], [_inc('A', ['room'])],
     _locks(U, U, U)),
    # incomplete Instructor
    ([_row('A', faculty_id=None, instructor='TBA')], [], [_inc('A', ['faculty'], fid=None)], _locks(U, U, U)),
    # incomplete Time/Days
    ([_row('A', time='', days='', days_list=[])], [], [_inc('A', ['schedule'])], _locks(U, U, U)),
], ids=['tba', 'tba+faculty', 'tba+time', 'tba+faculty+time', 'instructor', 'time_days'])
def test_incomplete_row_is_auto_selected_with_the_combined_locks(tmp_path, rows, conflicts, incomplete, expected):
    r = _run(_response(rows, conflicts=conflicts, incomplete=incomplete), tmp_path)
    assert r['initial']['A'] == expected
    assert r['initialGenerateCalls'] == 0


def test_conflict_only_row_is_not_auto_selected_but_select_conflict_rows_selects_it(tmp_path):
    rows = [_row('A'), _row('B', room_id=None, room='TBA', time='1:00 PM - 4:00 PM')]
    r = _run(_response(rows, conflicts=[_conflict('A', ['room', 'day', 'time'])],
                       incomplete=[_inc('B', ['room'])]), tmp_path)
    assert r['initial']['A'] == _NO                     # conflict only: left for the user
    assert r['initial']['B'] == _locks(U, U, U)         # incomplete: auto-selected, all unlocked
    assert r['selectConflictRows']['A'] == _locks(L, U, U)
