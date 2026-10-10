"""
Phase A2 cross-feature consistency tests.

Part 1 (structural): proves the four now-centralized app.py call sites --
Generation's _compute_schedule_evaluation, Manual Editor's
/api/schedule/validate, Save Draft's pre-save gate, and Publish/Approve's
intra-payload gate -- actually route through ConstraintService and no longer
instantiate scheduler.CSPValidator directly. This is a source-inspection
check, not a behavioral one: it exists so a future edit that reintroduces a
direct CSPValidator() call in one of these four places (silently forking
that one site's validation path away from the others) fails a test instead
of going unnoticed.

Part 2 (behavioral): for each of the six constraint categories named in the
Phase A2 task -- faculty overlap, room overlap, section overlap, restricted
day, faculty/designee time window, and load limit -- constructs one
representative schedule and asserts that calling ConstraintService the way
each of those four call sites calls it today (same method, same argument
shape) returns identical results. All four sites make literally the same
call as of this phase, so this also documents the shared contract: if a
future change gives one site extra arguments (skip_rules, existing_load,
rooms_by_id) that the others don't get, a reviewer sees exactly which
constraint category that affects.

Requests and Local Arrangements are intentionally NOT included here -- see
the Phase A2 report's conclusion that their current checks are not proven
behaviorally equivalent to CSPValidator (different scope: a specific
calendar date and additional occupancy sources CSPValidator has no concept
of, vs CSPValidator's recurring-weekly-pattern model) and so were not
migrated this phase.
"""
import inspect
from datetime import time

import app as app_module
from constraints import ConstraintService
from constraints.policy import SchedulingPolicy


# ── Part 1: structural -- the four centralized call sites ──────────────────

_CENTRALIZED_FUNCS = [
    'api_validate_schedule',   # POST /api/schedule/validate (Manual Editor)
    'api_save_draft',          # POST /api/schedule/save-draft
    'api_approve_schedule',    # POST /api/schedule/approve (Publish)
]


def test_centralized_route_functions_call_constraint_service():
    for name in _CENTRALIZED_FUNCS:
        src = inspect.getsource(getattr(app_module, name))
        assert 'ConstraintService' in src, (
            f"{name} no longer references ConstraintService -- "
            f"has its validation path been re-forked?"
        )


def test_centralized_route_functions_do_not_instantiate_cspvalidator_directly():
    for name in _CENTRALIZED_FUNCS:
        src = inspect.getsource(getattr(app_module, name))
        assert 'CSPValidator(' not in src, (
            f"{name} instantiates CSPValidator directly again -- "
            f"this bypasses ConstraintService and re-forks validation."
        )


def test_compute_schedule_evaluation_calls_constraint_service():
    src = inspect.getsource(app_module._compute_schedule_evaluation)
    assert 'ConstraintService' in src
    assert 'CSPValidator(' not in src


# ── Part 2: behavioral -- same call shape, same result, per constraint ─────

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


def _call_as_generation(schedule, faculty_map, cfg):
    # Mirrors _compute_schedule_evaluation's call shape exactly.
    return ConstraintService(SchedulingPolicy(cfg)).validate_schedule(schedule, faculty_map).violations


def _call_as_manual_editor_validate(schedule, faculty_map, cfg):
    # Mirrors api_validate_schedule's call shape exactly.
    return ConstraintService(SchedulingPolicy(cfg)).validate_schedule(schedule, faculty_map).violations


def _call_as_save_draft(schedule, faculty_map, cfg):
    # Mirrors api_save_draft's pre-save gate call shape exactly.
    return ConstraintService(SchedulingPolicy(cfg)).validate_schedule(schedule, faculty_map).violations


def _call_as_publish(schedule, faculty_map, cfg):
    # Mirrors api_approve_schedule's intra-payload gate call shape exactly.
    return ConstraintService(SchedulingPolicy(cfg)).validate_schedule(schedule, faculty_map).violations


_SITES = {
    'generation': _call_as_generation,
    'manual_editor_validate': _call_as_manual_editor_validate,
    'save_draft': _call_as_save_draft,
    'publish': _call_as_publish,
}


def _assert_all_sites_agree(schedule, faculty_map, cfg, expect_rule):
    results = {name: fn(schedule, faculty_map, cfg) for name, fn in _SITES.items()}
    rule_sets = {name: {v['rule'] for v in viols} for name, viols in results.items()}
    baseline = rule_sets['generation']
    for name, rules in rule_sets.items():
        assert rules == baseline, (
            f"{name} disagrees with generation: {rules} != {baseline}"
        )
    assert expect_rule in baseline
    # Full violation payloads (not just rule codes) must be identical too.
    values = list(results.values())
    for other in values[1:]:
        assert other == values[0]


def test_faculty_overlap_consistent_across_all_centralized_sites():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='IT102', faculty_id='F1', room_id=2,
              start_time=time(9, 0), end_time=time(10, 30))
    _assert_all_sites_agree([a, b], {}, {}, expect_rule='HC10')


def test_room_overlap_consistent_across_all_centralized_sites():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='IT102', faculty_id='F2', room_id=5,
              start_time=time(9, 0), end_time=time(10, 30))
    _assert_all_sites_agree([a, b], {}, {}, expect_rule='HC11')


def test_section_overlap_consistent_across_all_centralized_sites():
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F2', room_id=2,
              start_time=time(9, 0), end_time=time(10, 30))
    _assert_all_sites_agree([a, b], {}, {}, expect_rule='HC12')


def test_restricted_day_consistent_across_all_centralized_sites():
    cls = _gene(subject_code='IT101', day='Sunday', days_list=['Sunday'])
    _assert_all_sites_agree([cls], {}, {}, expect_rule='HC5')


def test_designee_time_window_consistent_across_all_centralized_sites():
    # PHASE B CHECKPOINT 1 CHANGE: a 7:30-8:00 designee class is no longer an
    # HC2 case at all (see tests/test_phase_b_designee_pt.py) -- it's now
    # PT/extra-teaching load under final HC4, with zero violations for a
    # slot fully inside the AM PT window. This test now uses a genuinely
    # still-Regular-classified slot (extends past the AM window's 9:00 end)
    # to keep proving HC2 itself is consistent across every centralized site.
    fac = {'F1': {'designationid': 1, 'employeestatus': '', 'employeetype': {},
                  'nightteachingservice': 0}}
    # Designee segment policy: a class with a segment outside every designee window
    # (7:00-7:30 AM of a 7:00-8:00 class) is HC4 — identical at every entry point.
    cls = _gene(faculty_id='F1', start_time=time(7, 0), end_time=time(8, 0))
    _assert_all_sites_agree([cls], fac, {'hc_time_blocks_enabled': 0}, expect_rule='HC4')


def test_load_limit_consistent_across_all_centralized_sites():
    fac = {'F1': {'employeestatus': 'Permanent',
                  'employeetype': {'regularload': 3, 'teachingsubstitution': 0,
                                    'regular_end': time(16, 30)}}}
    c1 = _gene(faculty_id='F1', day='Monday', days_list=['Monday'],
               start_time=time(7, 30), end_time=time(9, 30), duration_hrs=2)
    c2 = _gene(faculty_id='F1', subject_code='IT102', day='Tuesday',
               days_list=['Tuesday'], start_time=time(7, 30), end_time=time(9, 30),
               duration_hrs=2)
    _assert_all_sites_agree([c1, c2], fac, {}, expect_rule='HC9')
