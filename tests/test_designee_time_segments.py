"""Designee Extra Teaching Hours: classify and validate PER TIME SEGMENT.

A designee's class is split at the window boundaries before classifying:
  7:30-9:00 AM   special morning window -> Regular (TS once Regular is full)
  9:00 AM-4:30 PM                        -> Regular (TS once Regular is full)
  4:30-6:00 PM                           -> PT/TS
  6:00-9:00 PM   Night Teaching Service  -> PT (distinct-day limit: HC8)
A class crossing a boundary is never rejected as a whole; only a segment outside
every window is, and the message names that segment. The same split drives the
validator (Generation / Manual Editor / Save / Publish), the HC9 load buckets, the
Faculty Load totals, and the GA's allowed blocks."""
from datetime import time

import pytest

import faculty_load
from scheduler import CSPValidator
from constraints import ConstraintService
from constraints.policy import SchedulingPolicy

NO_GRID = {'hc_time_blocks_enabled': 0}


def _seg(start, end, day='Thursday'):
    return [(s.strftime('%H:%M'), e.strftime('%H:%M'), k)
            for s, e, k in faculty_load.designee_segments(day, start, end)]


# ── The examples ──────────────────────────────────────────────────────────────

def test_7_30_to_9_00_is_the_morning_window_only():
    assert _seg(time(7, 30), time(9, 0)) == [('07:30', '09:00', 'morning')]


def test_8_00_to_10_00_splits_at_9_00():
    assert _seg(time(8, 0), time(10, 0)) == [('08:00', '09:00', 'morning'), ('09:00', '10:00', 'regular')]


def test_8_00_to_9_30_splits_at_9_00():
    assert _seg(time(8, 0), time(9, 30)) == [('08:00', '09:00', 'morning'), ('09:00', '09:30', 'regular')]


def test_2_00_to_5_00_splits_at_4_30():
    assert _seg(time(14, 0), time(17, 0)) == [('14:00', '16:30', 'regular'), ('16:30', '17:00', 'pm')]


def test_into_the_night_window_and_outside_segments():
    assert _seg(time(17, 0), time(19, 0)) == [('17:00', '18:00', 'pm'), ('18:00', '19:00', 'night')]
    assert _seg(time(7, 0), time(8, 0)) == [('07:00', '07:30', 'outside'), ('07:30', '08:00', 'morning')]
    assert _seg(time(20, 0), time(21, 30)) == [('20:00', '21:00', 'night'), ('21:00', '21:30', 'outside')]
    assert _seg(time(9, 0), time(12, 0), day='Saturday') == [('09:00', '12:00', 'weekend')]


@pytest.mark.parametrize('start,end,reg,pt', [
    (time(7, 30), time(9, 0), 1.5, 0.0),
    (time(8, 0), time(10, 0), 2.0, 0.0),
    (time(8, 0), time(9, 30), 1.5, 0.0),
    (time(14, 0), time(17, 0), 2.5, 0.5),
])
def test_split_hours_regular_vs_pt(start, end, reg, pt):
    r, p, o = faculty_load.designee_split_hours('Thursday', start, end)
    assert (round(r, 2), round(p, 2), o) == (reg, pt, 0.0)


# ── Validation (one rule set for every entry point) ──────────────────────────

def _designee(**et):
    return {'F1': {'designationid': 1, 'employeestatus': 'Permanent', 'nightteachingservice': 0,
                   'fullname': 'Test Designee',
                   'employeetype': dict({'regularload': 99, 'parttimeload': 99, 'teachingsubstitution': 0}, **et)}}


def _gene(start, end, day='Thursday', code='ELED 209'):
    return {'subject_code': code, 'class_type': 'Lecture', 'course': 'BEED', 'faculty_id': 'F1',
            'room_id': 1, 'room_type': 'Lecture', 'day': day, 'days_list': [day],
            'start_time': start, 'end_time': end,
            'duration_hrs': (end.hour * 60 + end.minute - start.hour * 60 - start.minute) / 60}


def _rules(v):
    return {x['rule'] for x in v}


@pytest.mark.parametrize('start,end', [
    (time(7, 30), time(9, 0)), (time(8, 0), time(10, 0)),
    (time(8, 0), time(9, 30)), (time(14, 0), time(17, 0)),
])
def test_examples_are_not_rejected_by_hc2_or_hc4(start, end):
    v = CSPValidator(config=NO_GRID).validate([_gene(start, end)], _designee())
    assert not (_rules(v) & {'HC2', 'HC4'}), v


def test_screenshot_case_2_to_5_pm_thursday_now_publishes_through_the_central_gate():
    """ELED 209, Thursday 2:00-5:00 PM — used to be 'outside these windows' (HC4)."""
    v = ConstraintService(SchedulingPolicy(NO_GRID)).validate_schedule(
        [_gene(time(14, 0), time(17, 0))], _designee()).violations
    assert not (_rules(v) & {'HC2', 'HC4'})


def test_only_the_invalid_segment_is_reported():
    v = CSPValidator(config=NO_GRID).validate([_gene(time(20, 0), time(21, 30))],
                                              {'F1': dict(_designee()['F1'], nightteachingservice=3)})
    hc4 = [x for x in v if x['rule'] == 'HC4']
    assert len(hc4) == 1
    assert '09:00 PM–09:30 PM on Thursday' in hc4[0]['detail']
    assert '08:00 PM–09:00 PM' not in hc4[0]['detail'].split(' is outside')[0]


# ── HC9 buckets: Regular (TS when Regular is full) vs PT, per segment ────────

def test_2_to_5_pm_counts_2_5_regular_and_0_5_pt():
    reg_cap = _designee(regularload=2.4, parttimeload=0.5)
    v = [x for x in CSPValidator(config=NO_GRID).validate([_gene(time(14, 0), time(17, 0))], reg_cap)
         if x['rule'] == 'HC9']
    assert len(v) == 1 and 'regular load 2.5 hrs exceeds limit 2.4' in v[0]['detail']
    pt_cap = _designee(regularload=2.5, parttimeload=0.4)
    v = [x for x in CSPValidator(config=NO_GRID).validate([_gene(time(14, 0), time(17, 0))], pt_cap)
         if x['rule'] == 'HC9']
    assert len(v) == 1 and 'PT load 0.5 hrs exceeds limit 0.4' in v[0]['detail']


def test_regular_hours_beyond_the_regular_allocation_are_ts():
    full_regular_with_ts = _designee(regularload=0, parttimeload=0.5, teachingsubstitution=2.5)
    v = CSPValidator(config=NO_GRID).validate([_gene(time(14, 0), time(17, 0))], full_regular_with_ts)
    assert 'HC9' not in _rules(v)


def test_faculty_load_totals_use_the_same_split():
    sessions = [{'subjectcode': 'ELED 209', 'year_section': 'BEED-2', 'days': 'Thursday',
                 'time_code': '1417', 'time_range': '02:00 PM - 05:00 PM', 'hrs': 3.0,
                 'start': '14:00', 'end': '17:00'}]
    designee = faculty_load.summarize_faculty_load(sessions, designee=True)
    assert (designee['regular'], designee['pt']) == (2.5, 0.5)
    regular_faculty = faculty_load.summarize_faculty_load(sessions)   # non-designee: unchanged
    assert (regular_faculty['regular'], regular_faculty['pt']) == (0.0, 3.0)
    row = {'designationid': 1, 'typename': 'Permanent', 'regularload': 2.0, 'desig_reg_load': 2.0, 'parttimeload': 1.0,
           'teachingsubstitution': 1.0}
    b = faculty_load.compute_load_buckets(sessions, row)
    assert b['isDesignee'] and (b['regUsed'], b['ptUsed'], b['tsUsed']) == (2.0, 0.5, 0.5)


# ── Generation: the GA may place designee blocks that cross a boundary ───────

def test_generator_allows_crossing_blocks_for_designees():
    import app as app_module
    eng = app_module.scheduler_engine
    fac = dict(_designee()['F1'])
    blks = [(time(14, 0), time(17, 0)), (time(7, 30), time(9, 0)), (time(17, 0), time(19, 0))]
    allowed = {(s, e): k for s, e, k in eng._get_allowed_blocks_for_faculty(fac, blks)}
    assert allowed[(time(14, 0), time(17, 0))] == 'pt'        # has a PT/TS segment
    assert allowed[(time(7, 30), time(9, 0))] == 'regular'
    assert (time(17, 0), time(19, 0)) not in allowed          # night needs an allowance (HC8)
    fac['nightteachingservice'] = 2
    allowed = {(s, e) for s, e, _k in eng._get_allowed_blocks_for_faculty(fac, blks)}
    assert (time(17, 0), time(19, 0)) in allowed
