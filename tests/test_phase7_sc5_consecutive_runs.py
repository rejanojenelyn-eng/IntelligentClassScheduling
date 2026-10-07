"""
Constraint-fix Phase 7: SC5 (Avoid Excessive Consecutive Teaching) evaluates
continuous teaching RUNS instead of adjacent pairs.

  * same-day classes with a gap <= 15 minutes belong to one run;
  * a gap > 15 minutes starts a new run;
  * a run of two or more classes spanning >= 4 hours costs the SC5 weight once;
  * three or more back-to-back classes are one run.

Old pairwise behavior: `end(this) - start(previous) >= 240` for each adjacent
pair, regardless of the gap -- so two classes hours apart were charged, and
three back-to-back 1.5h classes (4.5h) were never charged.
"""
from datetime import time

from scheduler import teaching_runs, SC5_CONTIGUITY_GAP_MINUTES, SC5_EXCESSIVE_RUN_MINUTES
from test_characterization_soft_constraints import _sched, _gene, BASELINE_FAC

SC5_ONLY = {'hc_time_blocks_enabled': 0, 'sc7_consecutive': 30,
            'sc1_daytime': 0, 'sc4_compact': 0, 'sc3_day_dist': 0, 'sc5_pt_balance': 0,
            'sc6_weekend': 0, 'sc7_building': 0, 'sc9_specialization': 0}


def _c(code, start, end, day='Monday'):
    hrs = ((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)) / 60.0
    return _gene(subject_code=code, day=day, days_list=[day],
                 start_time=start, end_time=end, duration_hrs=hrs)


def _sc5_penalty(classes):
    score, n_viol = _sched(SC5_ONLY)._fitness(classes, BASELINE_FAC)
    assert n_viol == 0
    return 1000 - score


def test_thresholds_are_the_approved_values():
    assert SC5_CONTIGUITY_GAP_MINUTES == 15 and SC5_EXCESSIVE_RUN_MINUTES == 240


def test_three_back_to_back_classes_form_one_excessive_run():
    classes = [_c('A', time(7, 30), time(9, 0)), _c('B', time(9, 0), time(10, 30)),
               _c('C', time(10, 30), time(12, 0))]                  # 4.5h, every pair only 3h
    assert _sc5_penalty(classes) == 30


def test_large_gap_is_not_consecutive_teaching():
    classes = [_c('A', time(7, 30), time(9, 0)), _c('B', time(13, 30), time(15, 0))]
    assert _sc5_penalty(classes) == 0                               # old pairwise: 450 min -> charged


def test_gap_of_exactly_15_minutes_continues_the_run():
    classes = [_c('A', time(7, 30), time(9, 0)), _c('B', time(9, 15), time(10, 45)),
               _c('C', time(10, 45), time(11, 45))]                 # 4h15 continuous
    assert _sc5_penalty(classes) == 30


def test_gap_over_15_minutes_splits_the_run():
    classes = [_c('A', time(7, 30), time(9, 0)), _c('B', time(9, 20), time(11, 50))]
    assert _sc5_penalty(classes) == 0                               # 1.5h + 2.5h runs


def test_run_under_four_hours_is_acceptable():
    assert _sc5_penalty([_c('A', time(7, 30), time(9, 0)), _c('B', time(9, 0), time(10, 30))]) == 0


def test_each_excessive_run_is_charged_once():
    same_day = [_c('A', time(7, 0), time(9, 0)), _c('B', time(9, 0), time(11, 30)),
                _c('C', time(13, 0), time(15, 0)), _c('D', time(15, 0), time(17, 30))]
    assert _sc5_penalty(same_day) == 60                             # two 4.5h runs
    other_days = [_c('A', time(7, 0), time(9, 0)), _c('B', time(9, 0), time(11, 30)),
                  _c('C', time(7, 0), time(9, 0), day='Tuesday'),
                  _c('D', time(9, 0), time(11, 30), day='Tuesday')]
    assert _sc5_penalty(other_days) == 60


def test_a_single_long_class_is_not_consecutive_teaching():
    assert _sc5_penalty([_c('A', time(7, 0), time(12, 0))]) == 0


def test_teaching_runs_groups_by_day_and_gap_in_any_input_order():
    classes = [_c('C', time(10, 30), time(12, 0)), _c('A', time(7, 30), time(9, 0)),
               _c('X', time(9, 0), time(10, 30), day='Tuesday'), _c('B', time(9, 0), time(10, 30)),
               _c('D', time(14, 0), time(15, 0))]
    assert teaching_runs(classes) == [
        (450, 720, 3),     # Monday 7:30-12:00, three classes
        (840, 900, 1),     # Monday 14:00-15:00 after a 2h gap
        (540, 630, 1),     # Tuesday 9:00-10:30
    ]
