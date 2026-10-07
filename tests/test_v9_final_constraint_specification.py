from constraints.registry import HARD_CONSTRAINTS, SOFT_CONSTRAINTS


def test_final_constraint_inventory_is_stable():
    assert list(HARD_CONSTRAINTS) == [f"HC{i}" for i in range(1, 18)]
    assert list(SOFT_CONSTRAINTS) == [f"SC{i}" for i in range(1, 10)]


def test_hc14_is_conditional_and_does_not_guess_missing_enrollment():
    hc14 = HARD_CONSTRAINTS["HC14"]
    assert hc14["status"] == "conditional"
    text = (hc14["description"] + " " + hc14["notes"]).lower()
    assert "aggregate expected enrollment" in text
    assert "do not infer enrollment" in text


def test_cross_schedule_merge_and_load_special_statuses_are_explicit():
    assert HARD_CONSTRAINTS["HC15"]["status"] == "distributed"
    assert HARD_CONSTRAINTS["HC16"]["status"] == "decision_primitive"
    assert HARD_CONSTRAINTS["HC16"]["emits_violation"] is False
    assert HARD_CONSTRAINTS["HC17"]["status"] == "folded_into_HC9"
    assert HARD_CONSTRAINTS["HC17"]["emits_violation"] is False
