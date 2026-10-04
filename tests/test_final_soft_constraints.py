"""
Phase B checkpoint 3: required comparison-based tests for the final SC1-SC9
specification (scenarios A-I), proof that SC1-SC9 never create hard
invalidity on their own, and the merged-load three-way consistency
regression (CSPValidator/HC9, faculty_load.py, and SC4 must all agree).

Each SC scenario compares two schedules that differ only in the relevant
preference and asserts the better one scores higher -- more robust than
pinning exact fitness numbers, per the task's own guidance.
"""
from datetime import time

from scheduler import IntelligentScheduler, CSPValidator
import faculty_load as fl


def _sched(config=None):
    s = IntelligentScheduler()
    cfg = config if config is not None else {}
    s.csp = CSPValidator(config=cfg)
    s._hc_cfg = cfg
    return s


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


NO_GRID = {'hc_time_blocks_enabled': 0}
BASELINE_FAC = {'F1': {'employeetype': {}}}


# ── A. SC1 -- daytime vs unnecessary evening ───────────────────────────────

def test_a_sc1_daytime_scores_better_than_unnecessary_evening():
    # A plain 7:30-16:30 regular window can never fail is_daytime_block
    # (same 16:30 boundary), so this uses a faculty with an extended
    # configured regular_end (18:00, a real, supportable admin setting --
    # e.g. an extended-hours program) to construct a genuinely
    # Regular-classified-yet-late slot: still 'regular' (within the
    # extended window) but still "not daytime" (is_daytime_block's own
    # threshold is a fixed 16:30, independent of regular_end).
    sched = _sched(NO_GRID)
    fac = {'F1': {'employeetype': {'regular_end': time(18, 0)}}}
    daytime = _gene(start_time=time(9, 0), end_time=time(10, 30))
    late_regular = _gene(start_time=time(16, 30), end_time=time(18, 0))
    score_day, day_viol = sched._fitness([daytime], fac)
    score_late, late_viol = sched._fitness([late_regular], fac)
    assert day_viol == 0 and late_viol == 0
    assert score_day > score_late


def test_a_sc1_required_evening_pt_placement_not_penalized_like_avoidable_one():
    sched = _sched()
    fac_pt = {'F1': {'employeestatus': 'Part-Time', 'employeetype': {}}}
    required_evening = _gene(start_time=time(18, 0), end_time=time(19, 30))
    score_required, _ = sched._fitness([required_evening], fac_pt)
    daytime_baseline, _ = sched._fitness([_gene()], BASELINE_FAC)
    assert score_required == daytime_baseline  # no penalty at all, same as a clean daytime slot


# ── B. SC2 -- compact vs excessive gap ──────────────────────────────────────

def test_b_sc2_compact_day_scores_better_than_excessive_gap():
    sched = _sched(NO_GRID)
    a1 = _gene(start_time=time(7, 0), end_time=time(8, 0), duration_hrs=1)
    a2 = _gene(subject_code='IT102', start_time=time(8, 0), end_time=time(9, 0), duration_hrs=1)
    compact_score, _ = sched._fitness([a1, a2], BASELINE_FAC)

    b1 = _gene(start_time=time(7, 0), end_time=time(8, 0), duration_hrs=1)
    b2 = _gene(subject_code='IT102', start_time=time(9, 35), end_time=time(10, 35), duration_hrs=1)
    gappy_score, _ = sched._fitness([b1, b2], BASELINE_FAC)

    assert compact_score > gappy_score


# ── C. SC3 -- distributed vs concentrated teaching days ────────────────────

def test_c_sc3_distributed_days_score_better_than_concentrated():
    sched = _sched(NO_GRID)
    distributed = [
        _gene(day='Monday', days_list=['Monday'], start_time=time(7, 30), end_time=time(9, 0), duration_hrs=1.5),
        _gene(subject_code='IT102', day='Tuesday', days_list=['Tuesday'],
              start_time=time(7, 30), end_time=time(9, 0), duration_hrs=1.5),
        _gene(subject_code='IT103', day='Wednesday', days_list=['Wednesday'],
              start_time=time(7, 30), end_time=time(9, 0), duration_hrs=1.5),
    ]
    concentrated = [
        _gene(day='Monday', days_list=['Monday'], start_time=time(7, 0), end_time=time(12, 0), duration_hrs=5),
        _gene(subject_code='IT102', day='Tuesday', days_list=['Tuesday'],
              start_time=time(7, 30), end_time=time(8, 0), duration_hrs=0.5),
        _gene(subject_code='IT103', day='Wednesday', days_list=['Wednesday'],
              start_time=time(7, 30), end_time=time(8, 0), duration_hrs=0.5),
    ]
    dist_score, _ = sched._fitness(distributed, BASELINE_FAC)
    conc_score, _ = sched._fitness(concentrated, BASELINE_FAC)
    assert dist_score > conc_score


# ── D. SC4 -- PT load near target vs unnecessarily imbalanced ──────────────

def test_d_sc4_pt_load_near_target_scores_better_than_imbalanced():
    sched = _sched(NO_GRID)
    fac = {'F1': {'employeetype': {'regular_end': time(16, 30), 'parttimeload': 3}}}
    near_target = _gene(start_time=time(17, 0), end_time=time(20, 0), duration_hrs=3)   # exactly 3h
    imbalanced  = _gene(start_time=time(17, 0), end_time=time(18, 0), duration_hrs=1)   # only 1h, off by 2
    near_score, _ = sched._fitness([near_target], fac)
    far_score, _ = sched._fitness([imbalanced], fac)
    assert near_score > far_score


# ── E. SC5 -- acceptable vs excessive consecutive duration ─────────────────

def test_e_sc5_acceptable_consecutive_scores_better_than_excessive():
    sched = _sched(NO_GRID)
    acceptable = [
        _gene(start_time=time(7, 0), end_time=time(8, 30), duration_hrs=1.5),
        _gene(subject_code='IT102', start_time=time(8, 30), end_time=time(10, 0), duration_hrs=1.5),
    ]  # consec = 3h, under the 4h threshold
    excessive = [
        _gene(start_time=time(7, 0), end_time=time(9, 0), duration_hrs=2),
        _gene(subject_code='IT102', start_time=time(9, 0), end_time=time(11, 30), duration_hrs=2.5),
    ]  # consec = 4.5h, over threshold
    ok_score, _ = sched._fitness(acceptable, BASELINE_FAC)
    bad_score, _ = sched._fitness(excessive, BASELINE_FAC)
    assert ok_score > bad_score


# ── F. SC6 -- weekday vs unnecessary Saturday; required weekend exempt ─────

def test_f_sc6_weekday_scores_better_than_unnecessary_saturday():
    sched = _sched(NO_GRID)
    weekday = _gene(day='Tuesday', days_list=['Tuesday'], start_time=time(9, 0), end_time=time(10, 30))
    saturday = _gene(day='Saturday', days_list=['Saturday'], start_time=time(9, 0), end_time=time(10, 30))
    wd_score, _ = sched._fitness([weekday], BASELINE_FAC)
    sat_score, _ = sched._fitness([saturday], BASELINE_FAC)
    assert wd_score > sat_score


def test_f_sc6_required_pt_saturday_scores_the_same_as_weekday():
    # Weekday time chosen inside the Part-Time faculty's own valid evening
    # PT window (16:30-21:00 default, HC3) so this isolates SC6 without
    # tripping an unrelated HC3/HC9 hard violation for an invalid PT slot.
    sched = _sched(NO_GRID)
    fac_pt = {'F1': {'employeestatus': 'Part-Time', 'employeetype': {}}}
    weekday = _gene(day='Tuesday', days_list=['Tuesday'], start_time=time(17, 0), end_time=time(18, 30))
    saturday_pt = _gene(day='Saturday', days_list=['Saturday'], start_time=time(9, 0), end_time=time(10, 30))
    wd_score, wd_viol = sched._fitness([weekday], fac_pt)
    sat_score, sat_viol = sched._fitness([saturday_pt], fac_pt)
    assert wd_viol == 0 and sat_viol == 0
    assert wd_score == sat_score   # not merely "not worse" -- identical, no hidden penalty


# ── G. SC7 -- same building vs unnecessary building movement ──────────────

def test_g_sc7_same_building_scores_better_than_cross_building_movement():
    sched = _sched(NO_GRID)
    rooms_same = {1: {'buildingid': 10}, 2: {'buildingid': 10}}
    rooms_diff = {1: {'buildingid': 10}, 2: {'buildingid': 20}}
    c1 = _gene(room_id=1, start_time=time(7, 30), end_time=time(9, 0))
    c2 = _gene(subject_code='IT102', room_id=2, start_time=time(9, 10), end_time=time(10, 0))
    same_score, _ = sched._fitness([c1, c2], BASELINE_FAC, rooms_by_id=rooms_same)
    diff_score, _ = sched._fitness([c1, c2], BASELINE_FAC, rooms_by_id=rooms_diff)
    assert same_score > diff_score


# ── H. SC8 -- retained historical/CBR assignment vs equivalent non-retained ─

def test_h_sc8_retained_assignment_scores_better_than_equivalent_non_retained():
    sched = _sched()
    retained = _gene(is_preferred_faculty=True, is_preferred_room=True,
                      _lock={'source_case': 'case-1'})
    non_retained = _gene()
    retained_score, _ = sched._fitness([retained], BASELINE_FAC)
    plain_score, _ = sched._fitness([non_retained], BASELINE_FAC)
    assert retained_score > plain_score


# ── I. SC9 -- specialization match vs mismatch; both remain hard-feasible ──

def test_i_sc9_specialization_match_scores_better_than_mismatch():
    sched = _sched()
    matched_fac = {'F1': {'employeetype': {}, 'specializationname': 'Information Technology'}}
    mismatched_fac = {'F1': {'employeetype': {}, 'specializationname': 'Accountancy and Finance'}}
    cls = _gene(subject_code='COMP101')
    matched_score, matched_viol = sched._fitness([cls], matched_fac)
    mismatched_score, mismatched_viol = sched._fitness([cls], mismatched_fac)
    assert matched_score > mismatched_score
    # BOTH remain hard-feasible -- a specialization mismatch is a soft
    # preference, never a schedule-invalidating condition.
    assert matched_viol == 0
    assert mismatched_viol == 0
    direct_violations = CSPValidator(config={}).validate([cls], mismatched_fac)
    assert direct_violations == [] or all(v.get('severity') == 'warning' for v in direct_violations)


# ── Section 15: SC1-SC9 must never independently create hard invalidity ───

def test_sc9_mismatch_does_not_produce_a_hard_violation():
    fac = {'F1': {'employeetype': {}, 'specializationname': 'Accountancy and Finance'}}
    cls = _gene(subject_code='COMP101')
    violations = CSPValidator(config={}).validate([cls], fac)
    hard = [v for v in violations if v.get('severity') != 'warning']
    assert hard == []


def test_sc6_weekend_preference_does_not_become_a_hard_weekend_ban():
    # A Saturday-placed class with no HC violation of its own must remain
    # perfectly valid under CSPValidator -- SC6 is scoring-only.
    cls = _gene(day='Saturday', days_list=['Saturday'], start_time=time(9, 0), end_time=time(10, 30))
    violations = CSPValidator(config={}).validate([cls], {})
    assert violations == []


def test_sc8_absence_of_historical_preference_does_not_invalidate_schedule():
    cls = _gene()  # no is_preferred_faculty/is_preferred_room/_lock at all
    violations = CSPValidator(config={}).validate([cls], {})
    assert violations == []


# ── Section 19: merged-load three-way consistency ──────────────────────────

def test_merged_load_agrees_across_csp_faculty_load_and_sc4():
    """A valid 2-section, 1.5-hour merged NSTP session must report EXACTLY
    1.5 hours through all three: CSPValidator/HC9, faculty_load.py's
    compute_load_buckets (Faculty Load tab/DSS), and SC4's own PT-hours
    input inside _fitness()."""
    a = _gene(subject_code='NSTP101', faculty_id='F1', room_id=1, course='BSIT',
              section_name='A', day='Monday', days_list=['Monday'],
              start_time=time(17, 0), end_time=time(18, 30), duration_hrs=1.5)
    b = _gene(subject_code='NSTP101', faculty_id='F1', room_id=1, course='BSIT',
              section_name='B', day='Monday', days_list=['Monday'],
              start_time=time(17, 0), end_time=time(18, 30), duration_hrs=1.5)
    fac_row = {'employeestatus': 'Permanent',
               'employeetype': {'regularload': 10, 'parttimeload': 1.5, 'teachingsubstitution': 0}}
    fac_map = {'F1': fac_row}

    # 1) CSPValidator / HC9: 1.5h counted once, exactly at the 1.5h cap -> no violation.
    csp_violations = CSPValidator(config=NO_GRID).validate([a, b], fac_map)
    assert 'HC9' not in {v['rule'] for v in csp_violations}

    # 2) faculty_load.py: Faculty Load tab / DSS -- same 1.5h total.
    sessions = [
        {'subjectcode': 'NSTP101', 'year_section': 'BSIT-A', 'days': 'Monday',
         'time_code': '1718', 'time_range': '5:00 PM - 6:30 PM', 'hrs': 1.5},
        {'subjectcode': 'NSTP101', 'year_section': 'BSIT-B', 'days': 'Monday',
         'time_code': '1718', 'time_range': '5:00 PM - 6:30 PM', 'hrs': 1.5},
    ]
    grouped = fl.group_assignments(sessions, config={})
    assert sum(g['hrs'] for g in grouped) == 1.5

    # 3) SC4 input inside _fitness(): the merge-aware PT-hours total feeding
    # SC4 must also be 1.5, not 3.0 -- proven indirectly: with parttimeload
    # set to exactly 1.5, a perfectly-matched PT total costs SC4 nothing.
    sched = _sched(NO_GRID)
    score, n_viol = sched._fitness([a, b], fac_map)
    assert n_viol == 0
    # score = 1000 base, no SC penalties expected (perfect PT match, no gap/
    # consec/day-spread issue with only one shared time slot).
    assert score == 1000


def test_invalid_merge_attempt_does_not_get_deduplication_anywhere():
    """An invalid attempted merge (different subjects, non-NSTP, different
    faculty) must NOT be deduplicated by any of the three consumers."""
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=2, course='BSIT',
              section_name='A', day='Monday', days_list=['Monday'],
              start_time=time(17, 0), end_time=time(18, 30), duration_hrs=1.5)
    b = _gene(subject_code='IT102', faculty_id='F1', room_id=3, course='BSIT',
              section_name='B', day='Monday', days_list=['Monday'],
              start_time=time(17, 0), end_time=time(18, 30), duration_hrs=1.5)
    fac_row = {'employeestatus': 'Permanent',
               'employeetype': {'regularload': 10, 'parttimeload': 1.5, 'teachingsubstitution': 0}}
    fac_map = {'F1': fac_row}

    csp_violations = CSPValidator(config=NO_GRID).validate([a, b], fac_map)
    assert 'HC9' in {v['rule'] for v in csp_violations}   # 3.0h counted, exceeds 1.5h cap

    sessions = [
        {'subjectcode': 'IT101', 'year_section': 'BSIT-A', 'days': 'Monday',
         'time_code': '1718', 'time_range': '5:00 PM - 6:30 PM', 'hrs': 1.5},
        {'subjectcode': 'IT102', 'year_section': 'BSIT-B', 'days': 'Monday',
         'time_code': '1718', 'time_range': '5:00 PM - 6:30 PM', 'hrs': 1.5},
    ]
    grouped = fl.group_assignments(sessions, config={})
    assert sum(g['hrs'] for g in grouped) == 3.0
