"""
Characterization tests for IntelligentScheduler._fitness()'s FINAL SC1-SC9
behavior (Phase B checkpoint 3) plus the GA's penalty-based (not
reject/repair) handling of hard violations.

FINAL SC1-SC9 is active as of checkpoint 3 (see scheduler.py's _fitness()
docstring for the full design). Several of these tests replace earlier
characterization tests whose premises are now intentionally false --
each says so explicitly, with the reason.
"""
from datetime import time

from scheduler import IntelligentScheduler, CSPValidator


def _sched(config=None):
    """An IntelligentScheduler with an explicit CSP config AND an explicit
    _hc_cfg, so tests don't depend on whatever happens to be in the local
    scheduler_config table. Checkpoint 3 note: _fitness() now genuinely
    reads SC weights from self._hc_cfg (previously unused for this
    purpose), so pinning _hc_cfg explicitly (not just .csp) is required for
    determinism -- omitting it would leave weight lookups dependent on
    whatever the real DB's scheduler_config table currently holds.
    """
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


BASELINE_FAC = {'F1': {'employeetype': {}}}

# HC6 (standard 30-min grid) is enabled by default and several isolation tests
# below deliberately use off-grid times to hit exact minute boundaries -- this
# disables HC6 only, so those tests characterize _fitness() in isolation from
# it (the same technique test_csp_validator.py uses for HC6 itself).
_NO_GRID = {'hc_time_blocks_enabled': 0}


def test_baseline_single_daytime_class_scores_1000_with_no_violations():
    sched = _sched()
    score, n_viol = sched._fitness([_gene()], BASELINE_FAC)
    assert n_viol == 0
    assert score == 1000


# ── SC1 Minimize Unnecessary Night Classes (final; combines old SC1+SC2) ──

def test_sc1_regular_classified_non_daytime_block_deducts_weight():
    # PHASE B CHECKPOINT 3 CHANGE: a 16:30-18:00 slot for a faculty with a
    # blank employeestatus (this file's BASELINE_FAC) now classifies as PT
    # via the shared classifier (faculty_load.classify_assignment) -- it is
    # NOT a "regular class placed late", it's an extra-teaching-load slot,
    # exempt from SC1 by design. This test instead uses a class that stays
    # inside the plain 7:30-16:30 regular window but still fails the
    # daytime-block test... which is impossible under the default window
    # (regular hours end at 16:30, the same boundary is_daytime_block uses).
    # So instead: prove the EXEMPTION explicitly -- the old test's exact
    # scenario now costs nothing.
    sched = _sched()
    cls = _gene(start_time=time(16, 30), end_time=time(18, 0))  # PT-classified now
    score, n_viol = sched._fitness([cls], BASELINE_FAC)
    assert n_viol == 0
    assert score == 1000   # no SC1 penalty -- this is now a PT/extra-load slot


def test_sc1_does_not_penalize_part_time_faculty_evening_class():
    # Required scenario (checkpoint 3, "required evening PT placement should
    # not receive the unnecessary-night treatment"): a Part-Time faculty's
    # own evening class must never cost SC1, regardless of load
    # classification.
    sched = _sched()
    fac = {'F1': {'employeestatus': 'Part-Time', 'employeetype': {}}}
    cls = _gene(start_time=time(18, 0), end_time=time(19, 30))
    score, n_viol = sched._fitness([cls], fac)
    assert n_viol == 0
    assert score == 1000


# ── SC2 Minimize Faculty Schedule Gaps (final; was old SC4, threshold kept) ─

def test_sc2_gap_over_90_minutes_deducts_weight():
    sched = _sched(_NO_GRID)
    c1 = _gene(start_time=time(7, 0), end_time=time(8, 0), duration_hrs=1)
    c2 = _gene(subject_code='IT102', start_time=time(9, 35), end_time=time(10, 35),
               duration_hrs=1)
    score, n_viol = sched._fitness([c1, c2], BASELINE_FAC)
    assert n_viol == 0
    # gap = 8:00->9:35 = 95min (>90) -> SC2; consec = 215min (<240) -> no SC5
    assert score == 1000 - 10


# ── SC3 Balance Teaching-Day Distribution (final; unchanged) ───────────────

def test_sc3_one_heavy_day_among_three_deducts_weight():
    sched = _sched(_NO_GRID)
    c1 = _gene(day='Monday', days_list=['Monday'],
               start_time=time(7, 30), end_time=time(8, 0), duration_hrs=0.5)
    c2 = _gene(subject_code='IT102', day='Tuesday', days_list=['Tuesday'],
               start_time=time(7, 30), end_time=time(8, 0), duration_hrs=0.5)
    c3 = _gene(subject_code='IT103', day='Wednesday', days_list=['Wednesday'],
               start_time=time(7, 0), end_time=time(12, 0), duration_hrs=5)
    score, n_viol = sched._fitness([c1, c2, c3], BASELINE_FAC)
    assert n_viol == 0
    # avg = (0.5+0.5+5)/3 = 2.0; only Wednesday's 5.0 > avg*2 (4.0) -> one -10
    assert score == 1000 - 10


# ── SC4 Balance Faculty/Part-Time Load (final; was old SC5, merge-aware) ───

def test_sc4_pt_hours_deviation_deducts_weight_per_hour():
    sched = _sched(_NO_GRID)
    fac = {'F1': {'employeetype': {'regular_end': time(16, 30), 'parttimeload': 5}}}
    cls = _gene(start_time=time(17, 0), end_time=time(19, 0), duration_hrs=2)
    score, n_viol = sched._fitness([cls], fac)
    assert n_viol == 0
    # total_pt_hrs=2 (end>reg_end), |2-5|*10 = 30 for SC4. This slot is
    # PT-classified (start=17:00 is past the plain 16:30 regular_end), so
    # SC1's exemption applies -- no SC1 stacking here, unlike the pre-
    # checkpoint-3 behavior where SC1(old)+SC5(old) both fired.
    assert score == 1000 - 30


# ── SC5 Avoid Excessive Consecutive Teaching (final; was old SC7) ──────────

def test_sc5_four_hour_plus_consecutive_span_deducts_weight():
    sched = _sched(_NO_GRID)
    c1 = _gene(start_time=time(6, 0), end_time=time(8, 0), duration_hrs=2)
    c2 = _gene(subject_code='IT102', start_time=time(8, 0), end_time=time(10, 30),
               duration_hrs=2.5)
    score, n_viol = sched._fitness([c1, c2], BASELINE_FAC)
    assert n_viol == 0
    # gap = 0 (back-to-back) -> no SC2; consec = 10:30-6:00 = 270min -> SC5
    assert score == 1000 - 30


# ── SC6 Minimize Unnecessary Weekend Scheduling (final; REAL BEHAVIOR CHANGE) ─

def test_sc6_regular_faculty_weekend_classes_each_cost_the_weight():
    # PHASE B CHECKPOINT 3 CHANGE: the old SC6 balanced Saturday vs Sunday
    # COUNTS (a single -10 if the counts differed by more than 1) -- it never
    # discouraged weekend use itself. Final SC6 charges the weight PER
    # weekend session for a faculty with no availability-driven exemption,
    # so two Saturday sessions now cost -10 each (-20 total), not a single
    # balance -10.
    sched = _sched(_NO_GRID)
    c1 = _gene(day='Saturday', days_list=['Saturday'],
               start_time=time(7, 0), end_time=time(8, 0), duration_hrs=1)
    c2 = _gene(subject_code='IT102', day='Saturday', days_list=['Saturday'],
               start_time=time(9, 0), end_time=time(10, 0), duration_hrs=1)
    score, n_viol = sched._fitness([c1, c2], BASELINE_FAC)
    assert n_viol == 0
    assert score == 1000 - 20


def test_sc6_does_not_penalize_part_time_faculty_saturday_class():
    # Required scenario: PT faculty whose availability requires Saturday
    # must not receive the unnecessary-weekend penalty.
    sched = _sched(_NO_GRID)
    fac = {'F1': {'employeestatus': 'Part-Time', 'employeetype': {}}}
    cls = _gene(day='Saturday', days_list=['Saturday'],
                start_time=time(9, 0), end_time=time(10, 0), duration_hrs=1)
    score, n_viol = sched._fitness([cls], fac)
    assert n_viol == 0
    assert score == 1000


def test_sc6_does_not_penalize_nstp_sunday_class():
    # Required scenario: Sunday NSTP/OU may be hard-valid under HC5; SC6
    # must not additionally penalize it as "unnecessary weekend use".
    sched = _sched(_NO_GRID)
    cls = _gene(subject_code='NSTP101', day='Sunday', days_list=['Sunday'],
                start_time=time(9, 0), end_time=time(10, 0), duration_hrs=1)
    score, n_viol = sched._fitness([cls], BASELINE_FAC)
    assert n_viol == 0
    assert score == 1000


# ── SC7 Minimize Room/Building Movement (final; was unlabeled) ─────────────

def test_sc7_building_proximity_penalty_fires_on_tight_cross_building_gap():
    sched = _sched(_NO_GRID)
    c1 = _gene(room_id=1, start_time=time(7, 30), end_time=time(9, 0))
    c2 = _gene(subject_code='IT102', room_id=2,
               start_time=time(9, 10), end_time=time(10, 0))
    rooms_by_id = {1: {'buildingid': 10}, 2: {'buildingid': 20}}
    score, n_viol = sched._fitness([c1, c2], BASELINE_FAC, rooms_by_id=rooms_by_id)
    assert n_viol == 0
    # gap = 9:00->9:10 = 10min (<=15, tight transition) and different building -> -15
    assert score == 1000 - 15


def test_sc7_does_not_fire_within_same_building():
    sched = _sched(_NO_GRID)
    c1 = _gene(room_id=1, start_time=time(7, 30), end_time=time(9, 0))
    c2 = _gene(subject_code='IT102', room_id=2,
               start_time=time(9, 10), end_time=time(10, 0))
    rooms_by_id = {1: {'buildingid': 10}, 2: {'buildingid': 10}}
    score, n_viol = sched._fitness([c1, c2], BASELINE_FAC, rooms_by_id=rooms_by_id)
    assert n_viol == 0
    assert score == 1000


def test_sc7_contributes_no_score_when_building_data_unavailable():
    # "If location/building data are unavailable, SC7 should simply
    # contribute no score for that comparison" -- rooms_by_id omitted
    # entirely (empty), not fabricated.
    sched = _sched(_NO_GRID)
    c1 = _gene(room_id=1, start_time=time(7, 30), end_time=time(9, 0))
    c2 = _gene(subject_code='IT102', room_id=2,
               start_time=time(9, 10), end_time=time(10, 0))
    score, n_viol = sched._fitness([c1, c2], BASELINE_FAC, rooms_by_id={})
    assert n_viol == 0
    assert score == 1000


# ── SC8 Historical Assignment Retention (final; unchanged) ─────────────────

def test_sc8_preferred_faculty_room_and_cbr_lock_add_bonus():
    sched = _sched()
    cls = _gene(is_preferred_faculty=True, is_preferred_room=True,
                _lock={'source_case': 'case-1'})
    score, n_viol = sched._fitness([cls], BASELINE_FAC)
    assert n_viol == 0
    assert score == 1000 + 12 + 8 + 10


# ── SC9 Faculty Specialization Match (final; reclassified from HC_SPEC) ────

def test_sc9_matching_specialization_rewards_score():
    sched = _sched()
    fac = {'F1': {'employeetype': {}, 'specializationname': 'Information Technology'}}
    cls = _gene(subject_code='COMP101')
    score, n_viol = sched._fitness([cls], fac)
    assert n_viol == 0
    assert score == 1000 + 15


def test_sc9_mismatched_specialization_penalizes_score_not_hard_violation():
    sched = _sched()
    fac = {'F1': {'employeetype': {}, 'specializationname': 'Accountancy and Finance'}}
    cls = _gene(subject_code='COMP101')
    score, n_viol = sched._fitness([cls], fac)
    # CHECKPOINT 3 FIX (Phase B0 finding): a specialization mismatch used to
    # ALSO cost -200 as a hard violation via the raw self.csp.validate()
    # list (HC_SPEC's severity='warning' was not excluded). It no longer
    # does -- n_viol stays 0, only the SC9 penalty applies.
    assert n_viol == 0
    assert score == 1000 - 15


def test_sc9_blank_specialization_has_no_effect():
    sched = _sched()
    fac = {'F1': {'employeetype': {}, 'specializationname': ''}}
    cls = _gene(subject_code='COMP101')
    score, n_viol = sched._fitness([cls], fac)
    assert n_viol == 0
    assert score == 1000


# ── SC weight configurability (checkpoint 3: weights are now ACTIVE) ───────

def test_sc_weights_are_now_active_not_write_only():
    # PHASE B CHECKPOINT 3 CHANGE: this test used to be named
    # test_fitness_ignores_sc_weight_config_values_entirely and proved the
    # OPPOSITE -- that sc1_daytime/sc2_night were write-only dead config.
    # _fitness() now genuinely reads sc1_daytime (final SC1's weight).
    # Uses an extended regular_end (18:00) so this slot is genuinely
    # Regular-classified yet still fails is_daytime_block's fixed 16:30
    # threshold -- see test_final_soft_constraints.py's scenario A for why
    # a PLAIN 7:30-16:30 window can never exercise this branch.
    fac = {'F1': {'employeetype': {'regular_end': time(18, 0)}}}
    sched = _sched({'sc1_daytime': 999, 'hc_time_blocks_enabled': 0})
    cls = _gene(start_time=time(16, 30), end_time=time(18, 0))
    score, n_viol = sched._fitness([cls], fac)
    assert n_viol == 0
    assert score == 1000 - 999


def test_sc_weight_of_zero_disables_the_term_not_a_hardcoded_fallback():
    # Weight semantics (checkpoint 3): 0 must mean "disabled", never fall
    # back to a hardcoded default -- the same zero-vs-missing distinction
    # fixed for HC9's load caps in checkpoint 2 (cfg.get(key, default) only
    # falls back when the KEY IS ABSENT, not when its value is falsy).
    fac = {'F1': {'employeetype': {'regular_end': time(18, 0)}}}
    sched = _sched({'sc1_daytime': 0, 'hc_time_blocks_enabled': 0})
    cls = _gene(start_time=time(16, 30), end_time=time(18, 0))
    score, n_viol = sched._fitness([cls], fac)
    assert n_viol == 0
    assert score == 1000   # weight=0 -> no penalty, not the default 20


# ── GA hard-violation handling: penalty-based, not reject/repair ───────────

def test_hard_violations_reduce_score_by_200_each_and_individual_is_not_dropped():
    # Current behavior: _fitness() always returns a (score, violation_count)
    # pair for ANY individual, including one with hard violations -- it never
    # raises, returns None, or otherwise signals "discard this individual".
    # Selection/rejection (if any) happens elsewhere; _fitness itself is
    # purely a penalty, matching the audit's "penalty-based, not reject/
    # repair" finding. (Rule IDs are final HC11 room / HC12 section as of
    # checkpoint 2's renumbering -- only the count is asserted here.)
    sched = _sched()
    a = _gene(subject_code='IT101', faculty_id='F1', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code='MATH201', faculty_id='F2', room_id=1,
              start_time=time(9, 0), end_time=time(10, 30))
    fac = {'F1': {'employeetype': {}}, 'F2': {'employeetype': {}}}
    score, n_viol = sched._fitness([a, b], fac)
    assert n_viol == 2
    assert score == 1000 - 2 * 200
