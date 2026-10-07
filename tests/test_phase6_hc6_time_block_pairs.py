"""
Constraint-fix Phase 6 (HC6): a class time must be one whole valid block --
scheduler.STANDARD_BLOCKS plus the admin-configured hc_time_slots. A start and
an end that each exist in SOME block are not enough on their own.

15:00-18:00 was confirmed as an institutional block and is configured through
hc_time_slots (defaults + migrations/2026-09-30_hc6_time_slot_1500_1800.sql),
not special-cased in code. 10:30-13:00 and 7:30-12:30 are NOT configured.
"""
import json
from datetime import time

import pytest

import database
import scheduler
from scheduler import CSPValidator, STANDARD_BLOCKS
from conftest import requires_db

CONFIGURED = json.dumps([[7, 30, 9, 0], [9, 0, 10, 30], [15, 0, 18, 0]])


def _hc6(start, end, cfg=None):
    row = {'subject_code': 'GEED 006', 'day': 'Monday', 'days_list': ['Monday'],
           'start_time': start, 'end_time': end}
    return [v for v in CSPValidator(config=cfg if cfg is not None else {})._check_standard_slots([row])]


def test_every_standard_block_is_valid():
    for s, e in STANDARD_BLOCKS:
        assert _hc6(s, e) == [], (s, e)


def test_endpoints_from_different_blocks_are_not_a_block():
    # 10:30 starts a block and 13:00 ends one, but 10:30-13:00 is no block.
    v = _hc6(time(10, 30), time(13, 0))
    assert len(v) == 1 and 'is not a standard time block' in v[0]['detail']
    assert len(_hc6(time(7, 30), time(12, 30))) == 1


def test_configured_block_is_valid_only_when_configured():
    assert len(_hc6(time(15, 0), time(18, 0))) == 1                           # built-ins only
    assert _hc6(time(15, 0), time(18, 0), {'hc_time_slots': CONFIGURED}) == []


def test_off_grid_endpoints_keep_their_existing_messages():
    v = _hc6(time(14, 0), time(17, 0))
    assert len(v) == 2
    assert any('invalid start time' in x['detail'] for x in v)
    assert any('invalid end time' in x['detail'] for x in v)


def test_hc6_toggle_still_applies():
    row = {'subject_code': 'X', 'day': 'Monday', 'days_list': ['Monday'],
           'start_time': time(10, 30), 'end_time': time(13, 0)}
    rules = {v['rule'] for v in CSPValidator(config={'hc_time_blocks_enabled': 0}).validate([row], {})}
    assert 'HC6' not in rules


def test_adjacent_blocks_are_valid_and_do_not_overlap():
    a = {'subject_code': 'A', 'faculty_id': 'F1', 'room_id': 1, 'day': 'Monday',
         'days_list': ['Monday'], 'start_time': time(7, 30), 'end_time': time(9, 0)}
    b = dict(a, subject_code='B', start_time=time(9, 0), end_time=time(10, 30))
    rules = {v['rule'] for v in CSPValidator(config={}).validate([a, b], {})}
    assert not rules & {'HC6', 'HC10', 'HC11', 'HC12'}


def test_bad_configuration_never_mutates_the_module_grid():
    starts, ends = set(scheduler.VALID_START_TIMES), set(scheduler.VALID_END_TIMES)
    csp = CSPValidator(config={'hc_time_slots': 'not json'})
    csp._valid_starts.add(time(5, 55))
    assert scheduler.VALID_START_TIMES == starts and scheduler.VALID_END_TIMES == ends
    assert csp._valid_pairs == set(STANDARD_BLOCKS)


def test_default_configuration_includes_the_confirmed_block():
    slots = json.loads(database._SCHEDULER_CONFIG_DEFAULTS['hc_time_slots'])
    assert [15, 0, 18, 0] in slots
    assert [10, 30, 13, 0] not in slots and [7, 30, 12, 30] not in slots


@requires_db
def test_live_configuration_includes_the_confirmed_block():
    slots = json.loads(database.load_scheduler_config()['hc_time_slots'])
    assert [15, 0, 18, 0] in slots
    assert [10, 30, 13, 0] not in slots and [7, 30, 12, 30] not in slots


# ── Local inherits the same HC6 ─────────────────────────────────────────────

class _Cur:
    TIMES = {3: time(10, 30), 21: time(13, 0), 30: time(15, 0), 31: time(18, 0)}

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        if 'FROM public.timeslot' in self.sql:
            return [{'timeid': i, 'timevalue': self.TIMES[i]} for i in self.params if i in self.TIMES]
        return []

    def fetchone(self):
        if 'SELECT roomtype FROM public.room' in self.sql:
            return {'roomtype': 'Lecture'}
        if 'FROM public.curriculumsubject' in self.sql:
            return {'laboratoryhours': 0, 'lecturehours': 3}
        if 'FROM public.faculty f' in self.sql:
            return {'employeetypeid': 1, 'designationid': None}
        return None


@pytest.mark.parametrize('start,end,ok', [(3, 21, False), (30, 31, True)])
def test_local_adjustment_uses_the_same_block_pairs(monkeypatch, start, end, ok):
    import app as app_module
    monkeypatch.setattr(database, 'load_scheduler_config',
                        lambda: {'hc_time_slots': CONFIGURED, 'hc_time_blocks_enabled': 1})
    sess = {'subjectcode': 'IT101', 'daydesc': 'Monday', 'starttimeid': start,
            'endtimeid': end, 'roomid': 1}
    result, err = app_module._validate_local_editable_dimensions(_Cur(), sess, 'F1')
    assert result is ok, err
    if not ok:
        assert 'is not a standard time block' in err
