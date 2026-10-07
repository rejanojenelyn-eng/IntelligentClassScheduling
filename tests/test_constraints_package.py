"""
Equivalence tests for the constraints/ package (Phase A centralization).

These prove ConstraintService/hard_constraints/soft_constraints are pure
wrappers: for the same inputs, they must produce EXACTLY the same result as
calling scheduler.CSPValidator / IntelligentScheduler._fitness directly. If
any of these ever fail, the wrapper has drifted from the authoritative
implementation it's supposed to be a thin pass-through over.
"""
from datetime import time

from scheduler import CSPValidator, IntelligentScheduler
from constraints import ConstraintService, ValidationResult
from constraints.policy import SchedulingPolicy
from constraints import hard_constraints, soft_constraints


def _gene(**over):
    g = {
        'subject_code': 'IT101', 'class_type': 'Lecture', 'course': 'BSIT',
        'faculty_id': 'F1', 'room_id': 1, 'room_type': 'Lecture',
        'day': 'Monday', 'days_list': ['Monday'],
        'start_time': time(7, 30), 'end_time': time(9, 0),
        'duration_hrs': 1.5,
    }
    g.update(over)
    return g


def test_hard_constraints_validate_matches_direct_cspvalidator_call():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='IT102', faculty_id='F2', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))

    direct = CSPValidator(config={}).validate([a, b], {})
    via_adapter = hard_constraints.validate([a, b], {}, config={})

    assert via_adapter == direct


def test_constraint_service_validate_schedule_matches_direct_call():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='IT102', faculty_id='F2', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))

    direct = CSPValidator(config={}).validate([a, b], {})
    result = ConstraintService(SchedulingPolicy(config={})).validate_schedule([a, b], {})

    assert isinstance(result, ValidationResult)
    assert result.violations == direct
    assert result.has_violations is True
    assert {v['rule'] for v in result.hard_violations} == {'HC11', 'HC12'}


def test_constraint_service_validate_assignment_matches_manual_concat():
    context = [_gene(subject_code='IT101', faculty_id='F1', room_id=5,
                      start_time=time(9, 0), end_time=time(10, 30))]
    new_assignment = _gene(subject_code='IT102', faculty_id='F2', room_id=5,
                            start_time=time(9, 0), end_time=time(10, 30))

    direct = CSPValidator(config={}).validate(context + [new_assignment], {})
    result = ConstraintService(SchedulingPolicy(config={})).validate_assignment(
        new_assignment, context, {})

    assert result.violations == direct


def test_soft_constraints_score_matches_direct_fitness_call():
    fac = {'F1': {'employeetype': {}}}
    cls = _gene()

    sched = IntelligentScheduler()
    sched.csp = CSPValidator(config={})
    direct_score, direct_viol = sched._fitness([cls], fac)

    via_adapter = soft_constraints.score([cls], fac, config={})
    via_service = ConstraintService(SchedulingPolicy(config={})).score_soft_constraints([cls], fac)

    assert via_adapter == (direct_score, direct_viol)
    assert via_service == (direct_score, direct_viol)


def test_get_constraint_details_reports_final_hc_and_sc_inventory():
    # Final centralized constraint inventory:
    # HC1-HC17 are the formal hard constraints.
    # SC1-SC9 are the formal soft constraints.
    # Faculty specialization is SC9; legacy HC_SPEC is not part of HC1-HC17.
    details = ConstraintService(SchedulingPolicy(config={})).get_constraint_details()

    assert details['hard_constraint_ids'] == [
        'HC1', 'HC2', 'HC3', 'HC4', 'HC5', 'HC6', 'HC7', 'HC8', 'HC9', 'HC10',
        'HC11', 'HC12', 'HC13', 'HC14', 'HC15', 'HC16', 'HC17',
    ]

    assert details['soft_constraint_ids'] == [
        'SC1', 'SC2', 'SC3', 'SC4', 'SC5', 'SC6', 'SC7', 'SC8', 'SC9',
    ]

    # hc_section_conflict_enabled is live now (HC12 Settings switch); only the
    # program-restrict key remains dead.
    assert 'hc_section_conflict_enabled' not in details['dead_config_keys']
    assert 'hc_program_restrict_enabled' in details['dead_config_keys']

    # sc1_daytime is ACTIVE as final SC1's weight.
    # sc2_night is deprecated because the old night-class concept
    # was folded into final SC1.
    assert 'sc2_night' in details['write_only_config_keys']
    assert 'sc1_daytime' not in details['write_only_config_keys']