"""
Phase B checkpoint 1: tests for the ONE authoritative Regular-vs-PT load
classifier (faculty_load.classify_assignment / is_am_pt_window).

Before this checkpoint, three places disagreed about whether a designee's
7:30-9:00 AM class was Regular or PT (faculty_load.classify_slice said PT;
scheduler.CSPValidator._check_load_limits said Regular; _check_time_windows
had no answer for designees at all, only for full-time faculty). These tests
lock in the single correct answer for every faculty type.
"""
from datetime import time

from faculty_load import classify_assignment, is_am_pt_window


AM_START, AM_END = time(7, 30), time(9, 0)
FT_REG_START, FT_REG_END = time(7, 30), time(16, 30)
DESIGNEE_REG_START, DESIGNEE_REG_END = time(8, 0), time(17, 0)


# A. Full-Time weekday 7:30-9:00 -> PT
def test_a_fulltime_weekday_7_30_to_9_00_is_pt():
    result = classify_assignment(
        'Tuesday', time(7, 30), time(9, 0),
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'pt'


# B. Full-Time normal weekday regular slot -> Regular
def test_b_fulltime_normal_weekday_slot_is_regular():
    result = classify_assignment(
        'Tuesday', time(10, 30), time(12, 0),
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'regular'


# C. Full-Time evening PT -> PT
def test_c_fulltime_evening_is_pt():
    result = classify_assignment(
        'Tuesday', time(18, 0), time(19, 30),
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'pt'


# D. Designee weekday 7:30-9:00 -> PT
def test_d_designee_weekday_7_30_to_9_00_is_pt():
    # Classification uses the designee's plain employeetype window (per the
    # Phase B checkpoint 1 report -- classification and HC2's precise
    # boundary validation deliberately use different bounds), which is the
    # same shape as full-time's; the AM-PT window shortcut applies uniformly.
    result = classify_assignment(
        'Tuesday', time(7, 30), time(9, 0),
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'pt'


# E. Designee regular daytime slot -> Regular
def test_e_designee_regular_daytime_slot_is_regular():
    result = classify_assignment(
        'Tuesday', time(9, 30), time(11, 0),
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'regular'


# F. Designee 4:30-6:00 PM PT -> PT where applicable
def test_f_designee_4_30_to_6_00_pm_is_pt():
    result = classify_assignment(
        'Tuesday', time(16, 30), time(18, 0),
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'pt'


# G. Part-Time faculty appropriate assignment -> correct PT classification
def test_g_part_time_faculty_daytime_assignment_is_still_pt():
    # Part-time faculty have no Regular bucket at all, regardless of time.
    result = classify_assignment(
        'Tuesday', time(9, 0), time(10, 30), is_part_time=True,
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'pt'


# H. TS overflow -- not a classify_assignment concern; existing/final policy
# is that TS is an OVERFLOW applied afterward by cap_spill() once Regular/PT
# totals exceed their caps, never a property of one slice's time/day.
def test_h_ts_is_never_returned_by_the_classifier_itself():
    for start, end in [(time(7, 30), time(9, 0)), (time(10, 0), time(11, 30)),
                        (time(18, 0), time(19, 30))]:
        result = classify_assignment(
            'Tuesday', start, end,
            regular_start=FT_REG_START, regular_end=FT_REG_END,
            am_pt_start=AM_START, am_pt_end=AM_END,
        )
        assert result in ('regular', 'pt')


# ── is_am_pt_window direct tests ────────────────────────────────────────────

def test_is_am_pt_window_true_for_exact_default_window():
    assert is_am_pt_window('Tuesday', time(7, 30), time(9, 0), AM_START, AM_END) is True


def test_is_am_pt_window_false_when_start_before_window():
    assert is_am_pt_window('Tuesday', time(7, 0), time(8, 30), AM_START, AM_END) is False


def test_is_am_pt_window_false_when_end_after_window():
    assert is_am_pt_window('Tuesday', time(7, 30), time(9, 30), AM_START, AM_END) is False


def test_is_am_pt_window_false_on_weekend():
    assert is_am_pt_window('Saturday', time(7, 30), time(9, 0), AM_START, AM_END) is False


# ── Weekend/day-of-week always PT regardless of window ──────────────────────

def test_weekend_slot_is_always_pt():
    result = classify_assignment(
        'Saturday', time(9, 0), time(10, 30),
        regular_start=FT_REG_START, regular_end=FT_REG_END,
        am_pt_start=AM_START, am_pt_end=AM_END,
    )
    assert result == 'pt'
