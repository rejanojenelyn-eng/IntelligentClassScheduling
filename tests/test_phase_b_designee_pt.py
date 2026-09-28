"""
Phase B checkpoint 1/2: designee AM-PT regression tests, HC8 (final)
night-count tests, and HC9 (final) load-bucket cross-validation.

Core scenario this whole checkpoint exists for: a designee's Tuesday/Friday
7:30-9:00 AM assignment must be PT/extra-teaching load, never Regular, and
must never be double-counted or miscounted as a teaching "night".

FINAL HC1-HC17 numbering is active as of checkpoint 2 -- the night-cap rule
is final HC8 (was internal 'HC7'), the load-limit rule is final HC9 (was
internal 'HC8'), and the designee PT-window rule is final HC4 (was internal
'HC_DESIGNEE_PT').
"""
from datetime import time

from scheduler import CSPValidator
from constraints import ConstraintService
from constraints.policy import SchedulingPolicy


def _gene(**over):
    g = {
        'subject_code': 'IT101', 'class_type': 'Lecture', 'course': 'BSIT',
        'faculty_id': 'F1', 'room_id': 1, 'room_type': 'Lecture',
        'day': 'Tuesday', 'days_list': ['Tuesday'],
        'start_time': time(7, 30), 'end_time': time(9, 0),
        'duration_hrs': 1.5,
    }
    g.update(over)
    return g


def _designee(**over):
    fac = {
        'designationid': 1, 'employeestatus': '', 'employeetype': {},
        'nightteachingservice': 0, 'fullname': 'Test Designee',
    }
    fac.update(over)
    return fac


def _rules(v):
    return {x['rule'] for x in v}


NO_GRID = {'hc_time_blocks_enabled': 0}


# ── §13: Designee 7:30-9:00 AM regression ───────────────────────────────────

def test_designee_tuesday_friday_7_30_to_9_00_am_produces_zero_violations():
    fac = {'F1': _designee()}
    tue = _gene(day='Tuesday', days_list=['Tuesday'])
    fri = _gene(day='Friday', days_list=['Friday'], subject_code='IT102')
    v = CSPValidator(config={}).validate([tue, fri], fac)
    assert v == []


def test_designee_tuesday_8_00_to_10_00_regular_slot_is_evaluated_normally():
    fac = {'F1': _designee()}
    cls = _gene(start_time=time(8, 0), end_time=time(10, 0))
    v = CSPValidator(config=NO_GRID).validate([cls], fac)
    assert 'HC2' not in _rules(v)
    assert 'HC4' not in _rules(v)


def test_designee_invalid_early_7_00_to_8_30_am_fails():
    # Must NOT become valid merely because it's early -- it fails the
    # appropriate time-window constraint (final HC4).
    fac = {'F1': _designee()}
    cls = _gene(start_time=time(7, 0), end_time=time(8, 30))
    v = CSPValidator(config=NO_GRID).validate([cls], fac)
    assert 'HC4' in _rules(v)


# ── §14: HC8 (final) unique-teaching-night tests ────────────────────────────

def test_two_pt_classes_same_evening_count_as_one_night():
    fac = {'F1': _designee()}
    a = _gene(day='Monday', days_list=['Monday'],
              start_time=time(16, 30), end_time=time(17, 15))
    b = _gene(day='Monday', days_list=['Monday'], subject_code='IT102',
              room_id=2, start_time=time(17, 15), end_time=time(18, 0))
    v = CSPValidator(config=NO_GRID).validate([a, b], fac)
    assert 'HC8' not in _rules(v)   # 4:30-6:00 PM never consumes a night


def test_two_different_pt_evenings_is_allowed():
    fac = {'F1': _designee()}
    mon = _gene(day='Monday', days_list=['Monday'],
                start_time=time(16, 30), end_time=time(18, 0))
    tue = _gene(day='Tuesday', days_list=['Tuesday'], subject_code='IT102',
                start_time=time(16, 30), end_time=time(18, 0))
    v = CSPValidator(config=NO_GRID).validate([mon, tue], fac)
    assert 'HC8' not in _rules(v)


def test_three_4_30_to_6_00_pm_evenings_do_not_consume_night_days():
    # DESIGNEE_NIGHT_TEACHING_DAY_LIMIT: only 6:00-9:00 PM consumes a night.
    # This used to violate under the retired flat hc7_max_night cap, which
    # counted 4:30-6:00 PM as a night too.
    fac = {'F1': _designee()}   # allowance 0
    mon = _gene(day='Monday', days_list=['Monday'],
                start_time=time(16, 30), end_time=time(18, 0))
    tue = _gene(day='Tuesday', days_list=['Tuesday'], subject_code='IT102',
                start_time=time(16, 30), end_time=time(18, 0))
    wed = _gene(day='Wednesday', days_list=['Wednesday'], subject_code='IT103',
                start_time=time(16, 30), end_time=time(18, 0))
    v = CSPValidator(config=NO_GRID).validate([mon, tue, wed], fac)
    assert 'HC8' not in _rules(v)
    assert 'HC4' not in _rules(v)


def test_morning_pt_plus_two_evening_nights_still_allowed():
    fac = {'F1': _designee(nightteachingservice=2)}
    am  = _gene(day='Thursday', days_list=['Thursday'], subject_code='IT104')  # 7:30-9:00
    mon = _gene(day='Monday', days_list=['Monday'], subject_code='IT102',
                start_time=time(18, 0), end_time=time(21, 0))
    tue = _gene(day='Tuesday', days_list=['Tuesday'], subject_code='IT103',
                start_time=time(18, 0), end_time=time(21, 0))
    v = CSPValidator(config=NO_GRID).validate([am, mon, tue], fac)
    assert 'HC8' not in _rules(v)   # AM never counts as a night -> still 2


def test_morning_pt_plus_three_evening_nights_violates():
    fac = {'F1': _designee(nightteachingservice=2)}
    am  = _gene(day='Thursday', days_list=['Thursday'], subject_code='IT104')
    mon = _gene(day='Monday', days_list=['Monday'], subject_code='IT102',
                start_time=time(18, 0), end_time=time(21, 0))
    tue = _gene(day='Tuesday', days_list=['Tuesday'], subject_code='IT103',
                start_time=time(18, 0), end_time=time(21, 0))
    wed = _gene(day='Wednesday', days_list=['Wednesday'], subject_code='IT105',
                start_time=time(18, 0), end_time=time(21, 0))
    v = CSPValidator(config=NO_GRID).validate([am, mon, tue, wed], fac)
    assert 'HC8' in _rules(v)


def test_hc8_cap_is_the_designation_value_not_hc7_max_night():
    # The cap is the designation's PT/Night Teaching Service value; the
    # legacy global hc7_max_night key no longer controls HC8 either way.
    mon = _gene(day='Monday', days_list=['Monday'],
                start_time=time(18, 0), end_time=time(21, 0))
    tue = _gene(day='Tuesday', days_list=['Tuesday'], subject_code='IT102',
                start_time=time(18, 0), end_time=time(21, 0))
    wed = _gene(day='Wednesday', days_list=['Wednesday'], subject_code='IT103',
                start_time=time(18, 0), end_time=time(21, 0))
    cfg = dict(NO_GRID); cfg['hc7_max_night'] = 3
    v = CSPValidator(config=cfg).validate([mon, tue, wed], {'F1': _designee(nightteachingservice=2)})
    assert 'HC8' in _rules(v)

    cfg2 = dict(NO_GRID); cfg2['hc7_max_night'] = 1
    v2 = CSPValidator(config=cfg2).validate([mon, tue, wed], {'F1': _designee(nightteachingservice=3)})
    assert 'HC8' not in _rules(v2)


# ── §15: HC9 (final) load-bucket cross-validation ───────────────────────────

def test_designee_am_pt_hours_land_entirely_in_pt_bucket_not_regular():
    # Tuesday + Friday 7:30-9:00 AM = 3.0 hours total. regularload is set so
    # low that ANY regular-bucketed hours would violate; parttimeload is set
    # just under 3.0 so the PT bucket violating proves exactly 3.0 hours
    # landed there. If the old bug were still present (AM slice miscounted
    # as Regular), this assertion pair would flip.
    fac = {'F1': _designee(employeetype={
        'regularload': 0.1, 'parttimeload': 2.9, 'teachingsubstitution': 0,
    })}
    tue = _gene(day='Tuesday', days_list=['Tuesday'])
    fri = _gene(day='Friday', days_list=['Friday'], subject_code='IT102')
    v = CSPValidator(config=NO_GRID).validate([tue, fri], fac)
    hc9_viols = [x for x in v if x['rule'] == 'HC9']
    assert len(hc9_viols) == 1
    assert 'PT load 3.0 hrs exceeds limit 2.9' in hc9_viols[0]['detail']
    assert 'regular load' not in hc9_viols[0]['detail'].lower()


def test_regular_bucket_is_genuinely_zero_for_am_only_designee_schedule():
    fac = {'F1': _designee(employeetype={
        'regularload': 0.0, 'parttimeload': 99, 'teachingsubstitution': 0,
    })}
    tue = _gene(day='Tuesday', days_list=['Tuesday'])
    fri = _gene(day='Friday', days_list=['Friday'], subject_code='IT102')
    v = CSPValidator(config=NO_GRID).validate([tue, fri], fac)
    # regularload=0 -- if even a fraction of an hour were miscounted as
    # Regular, this would violate. It doesn't: all 3.0 hours are PT.
    assert 'HC9' not in _rules(v)


def test_same_assignment_produces_identical_result_via_constraint_service():
    """HC9 must not have separate interpretations across entry points: the
    exact same schedule/config produces the exact same violations whether
    called directly via CSPValidator or via the centralized ConstraintService
    (used by Generation, Manual Editor's /api/schedule/validate, Save Draft,
    and Publish per Phase A/A2)."""
    fac = {'F1': _designee(employeetype={
        'regularload': 0.1, 'parttimeload': 2.9, 'teachingsubstitution': 0,
    })}
    tue = _gene(day='Tuesday', days_list=['Tuesday'])
    fri = _gene(day='Friday', days_list=['Friday'], subject_code='IT102')

    direct = CSPValidator(config=NO_GRID).validate([tue, fri], fac)
    via_service = ConstraintService(SchedulingPolicy(NO_GRID)).validate_schedule(
        [tue, fri], fac
    ).violations
    assert via_service == direct


# ── §23: Checkpoint 1 designee regression, retained through renumbering ────

def test_checkpoint1_designee_regression_scenario_still_holds():
    """The exact scenario Phase B checkpoint 1 was built for, re-verified
    after checkpoint 2's renumbering: DESIGNEE, Tuesday + Friday,
    7:30 AM-9:00 AM. Must remain: load type PT, HC2 not applicable, HC4
    pass, HC8 night count 0, HC9 Regular 0.0h / PT 3.0h."""
    fac = {'F1': _designee(employeetype={
        'regularload': 0.1, 'parttimeload': 2.9, 'teachingsubstitution': 0,
    })}
    tue = _gene(day='Tuesday', days_list=['Tuesday'])
    fri = _gene(day='Friday', days_list=['Friday'], subject_code='IT102')
    v = CSPValidator(config=NO_GRID).validate([tue, fri], fac)

    assert 'HC2' not in _rules(v)   # not applicable -- PT-classified
    assert 'HC4' not in _rules(v)   # PASS -- within the AM PT window
    assert 'HC8' not in _rules(v)   # 0 teaching nights (AM never counts)
    hc9 = [x for x in v if x['rule'] == 'HC9']
    assert len(hc9) == 1
    assert 'PT load 3.0 hrs exceeds limit 2.9' in hc9[0]['detail']
