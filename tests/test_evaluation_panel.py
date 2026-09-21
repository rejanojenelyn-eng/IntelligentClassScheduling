"""
Integration tests for _compute_schedule_evaluation() (architecture spec section
12): N/A handling when no historical case exists, completionRate/
hardConstraintCompliance/incompleteCount fields, and eligibleForApproval
requiring full completeness (not just zero hard violations). Needs the real
database (the function itself queries historical_data/schedule tables), and
needs `app` importable (which applies the startup migration once).
"""
from datetime import time

from conftest import requires_db


@requires_db
def test_no_historical_case_reports_na_not_a_fake_percentage():
    import app

    schedule_data = [{
        "subject_code": "ZZZNOPE1", "instructor": "Nobody, N.",
        "room": "LQ999", "start_time": time(7, 30), "end_time": time(9, 0),
        "days_list": ["Monday"],
    }]
    result = app._compute_schedule_evaluation(
        schedule_data, "ZZZ_NO_SUCH_PROGRAM_9999", 1, "A",
    )
    rec = result["categories"]["recommendationQuality"]["criteria"]
    assert rec["historicalFacultyMatch"] == "N/A"
    assert rec["historicalRoomMatch"] == "N/A"
    # A schedule with no usable historical case must never score 0% OR a
    # fabricated 100% on these criteria — 'N/A' is the only correct value.
    assert rec["historicalFacultyMatch"] != 0
    assert rec["historicalFacultyMatch"] != 100.0


@requires_db
def test_na_criteria_do_not_corrupt_the_overall_score():
    import app

    schedule_data = [{
        "subject_code": "ZZZNOPE2", "instructor": "Nobody, N.",
        "room": "LQ999", "start_time": time(7, 30), "end_time": time(9, 0),
        "days_list": ["Monday"],
    }]
    result = app._compute_schedule_evaluation(
        schedule_data, "ZZZ_NO_SUCH_PROGRAM_9999", 1, "A",
    )
    # Score must stay a plausible 0-100 int even with 3 of 11 sub-criteria N/A.
    assert isinstance(result["overallScore"], int)
    assert 0 <= result["overallScore"] <= 100


@requires_db
def test_eligible_for_approval_requires_zero_incomplete_even_if_csp_passes():
    import app

    schedule_data = [{
        "subject_code": "ZZZNOPE3", "instructor": "Nobody, N.",
        "room": "LQ999", "start_time": time(7, 30), "end_time": time(9, 0),
        "days_list": ["Monday"],
    }]
    complete_eval = app._compute_schedule_evaluation(
        schedule_data, "ZZZ_NO_SUCH_PROGRAM_9999", 1, "A", incomplete_count=0,
    )
    partial_eval = app._compute_schedule_evaluation(
        schedule_data, "ZZZ_NO_SUCH_PROGRAM_9999", 1, "A", incomplete_count=2,
    )
    assert complete_eval["cspPassed"] == partial_eval["cspPassed"]  # same CSP result either way
    if complete_eval["cspPassed"]:
        assert complete_eval["eligibleForApproval"] is True
    # A schedule with incomplete components is NEVER publish-eligible, no
    # matter how clean the completed portion is.
    assert partial_eval["eligibleForApproval"] is False
    assert partial_eval["incompleteCount"] == 2


@requires_db
def test_completion_rate_passthrough_defaults_to_complete_for_legacy_callers():
    import app

    schedule_data = [{
        "subject_code": "ZZZNOPE4", "instructor": "Nobody, N.",
        "room": "LQ999", "start_time": time(7, 30), "end_time": time(9, 0),
        "days_list": ["Monday"],
    }]
    result = app._compute_schedule_evaluation(
        schedule_data, "ZZZ_NO_SUCH_PROGRAM_9999", 1, "A",
    )
    assert result["completionRate"] == 100.0
    assert result["incompleteCount"] == 0


@requires_db
def test_hard_constraint_compliance_measured_over_completed_only():
    import app

    schedule_data = [{
        "subject_code": "ZZZNOPE5", "instructor": "Nobody, N.",
        "room": "LQ999", "start_time": time(7, 30), "end_time": time(9, 0),
        "days_list": ["Monday"],
    }]
    # A schedule can be partially complete and still show 100% hard-constraint
    # compliance among what it DID complete (architecture spec section 12) —
    # this must be true even though eligibleForApproval is separately False.
    result = app._compute_schedule_evaluation(
        schedule_data, "ZZZ_NO_SUCH_PROGRAM_9999", 1, "A", incomplete_count=1,
    )
    assert result["hardConstraintCompliance"] == 100.0
    assert result["eligibleForApproval"] is False
