"""
Fix #3 — HC6 Standard Time-Slot Compliance investigation.

Findings pinned here:
  * The "invalid start/end time ... Please select a standard time block" messages
    come only from CSPValidator._check_standard_slots (final HC6).
  * Allowed endpoints = Settings `hc_time_slots` ∪ scheduler.STANDARD_BLOCKS
    (the generator's own block list). The Settings grid is a subset of
    STANDARD_BLOCKS, so the effective grid is exactly what generation produces.
  * The grid is day-independent: Saturday uses the same grid as weekdays.
    (Weekend SERVICE WINDOWS are a separate rule — HC3/HC5 — not HC6.)
  * 2:00 PM start / 5:00 PM end is on neither grid, so a historical
    "SAT 2:00 - 5:00" class is a legitimate current-AY HC6 violation.
"""
import json
from datetime import time

import pytest

import scheduler
from scheduler import CSPValidator, STANDARD_BLOCKS, get_blocks_for_hours, duration_hours
from constraints import ConstraintService
from constraints.policy import SchedulingPolicy
from conftest import requires_db

# Mirrors the live Settings value (all pairs are also in STANDARD_BLOCKS).
SETTINGS_GRID = json.dumps([[7, 30, 9, 0], [9, 0, 10, 30], [10, 30, 12, 0], [12, 0, 13, 30],
                            [13, 30, 15, 0], [15, 0, 16, 30], [16, 30, 18, 0], [18, 0, 19, 30],
                            [19, 30, 21, 0], [10, 30, 13, 30], [13, 30, 16, 30], [7, 30, 10, 30],
                            [9, 0, 12, 0], [16, 30, 19, 30], [18, 0, 21, 0]])
CFG = {'hc_time_slots': SETTINGS_GRID, 'hc_day_pairing_enabled': 0}


def _row(day, start, end, code='GEED 006'):
    return {'subject_code': code, 'day': day, 'days_list': [day],
            'start_time': start, 'end_time': end}


def _hc6(rows, cfg=CFG):
    viols = ConstraintService(SchedulingPolicy(cfg)).validate_schedule(rows, {}).violations
    return [v for v in viols if v['rule'] == 'HC6']


def test_b1_every_block_the_generator_can_produce_passes_hc6():
    durations = {round(duration_hours(s, e), 2) for (s, e) in STANDARD_BLOCKS}
    produced = set(STANDARD_BLOCKS)
    for d in durations:
        produced |= set(get_blocks_for_hours(d)) | set(get_blocks_for_hours(d, is_lab=True))
    for (s, e) in produced:
        for day in ('Monday', 'Saturday'):
            assert _hc6([_row(day, s, e)]) == [], (day, s, e)


def test_b1b_settings_grid_adds_nothing_the_generator_cannot_produce():
    starts, ends = scheduler._parse_time_slots(SETTINGS_GRID)
    assert starts <= scheduler.VALID_START_TIMES and ends <= scheduler.VALID_END_TIMES


@requires_db
def test_b2_manual_editor_validation_endpoint_uses_the_same_rule():
    import app as app_module
    client = app_module.app.test_client()
    bad = client.post('/api/schedule/validate', json={'schedule_data': [
        {'subject_code': 'GEED 006', 'day': 'Saturday', 'days_list': ['Saturday'],
         'start_time': '02:00 PM', 'end_time': '05:00 PM', 'room_type': 'Lecture'}]}).get_json()
    assert {v['rule'] for v in bad['violations']} >= {'HC6'}
    good = client.post('/api/schedule/validate', json={'schedule_data': [
        {'subject_code': 'GEED 006', 'day': 'Saturday', 'days_list': ['Saturday'],
         'start_time': '01:30 PM', 'end_time': '04:30 PM', 'room_type': 'Lecture'}]}).get_json()
    assert 'HC6' not in {v['rule'] for v in good['violations']}


def test_b3_retrieved_historical_time_is_evaluated_by_the_current_rule():
    import app as app_module
    days, s, e, status, _ = app_module._resolve_historical_day_time('SAT', '2:00 - 5:00')
    assert (days, status) == (['Saturday'], 'resolved')
    msgs = [v['detail'] for v in _hc6([_row(days[0], s, e)])]
    assert any('invalid start time (02:00 PM)' in m for m in msgs)
    assert any('invalid end time (05:00 PM)' in m for m in msgs)


def test_b4_duration_is_three_hours():
    import app as app_module
    _d, s, e, _st, _ = app_module._resolve_historical_day_time('SAT', '2:00 - 5:00')
    assert (s, e) == (time(14, 0), time(17, 0))
    assert duration_hours(s, e) == 3.0


def test_b5_saturday_uses_the_same_grid_as_weekdays():
    for day in ('Monday', 'Saturday'):
        assert _hc6([_row(day, time(13, 30), time(16, 30))]) == []
        assert len(_hc6([_row(day, time(14, 0), time(17, 0))])) == 2


def test_b6_genuinely_invalid_historical_time_stays_flagged():
    import app as app_module
    _d, s, e, _st, _ = app_module._resolve_historical_day_time('SAT', '5:30 - 8:30')
    assert _hc6([_row('Saturday', s, e)])


def test_b7_valid_current_time_is_not_flagged():
    import app as app_module
    _d, s, e, _st, _ = app_module._resolve_historical_day_time('SAT', '10:30 - 1:30')
    assert (s, e) == (time(10, 30), time(13, 30))
    assert _hc6([_row('Saturday', s, e)]) == []


@requires_db
def test_b3b_retrieve_previous_reports_hc6_for_bpa_saturday_2_to_5():
    import app as app_module
    body = app_module.app.test_client().post('/api/schedule/retrieve-previous', json={
        'program': 'BPA', 'yearLevel': 2, 'term': 'A', 'acadYear': 'AY2627',
        'section': '14', 'curriculum': '2022-2023'}).get_json()
    if not body.get('success'):
        pytest.skip('no BPA 2 previous-AY history available')
    geed = [r for r in body['schedule_data'] if r['subject_code'] == 'GEED 006']
    if not geed or geed[0]['start_time'] != '14:00':
        pytest.skip('BPA 2 history no longer has GEED 006 SAT 2:00-5:00')
    hc6 = [c for c in body['evaluation']['conflicts']
           if c['rule'] == 'HC6' and 'GEED 006' in c['subject']]
    assert hc6   # current rule applied; historical time preserved (not rewritten)
