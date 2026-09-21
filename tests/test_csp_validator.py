"""
Unit tests for CSPValidator changes made in this pass (no database required):
  - HC5 (30-minute grid) is now actually dispatched by validate() — it existed
    as a fully-implemented method (_check_standard_slots) but was never called.
  - HC_CAPACITY (room capacity) is a new rule, safely a no-op without class-size
    data, and fires correctly when that data IS present.
  - validate() no longer crashes on an incomplete gene (start_time=None) — the
    partial-generation path (generate_draft's _strip_component) intentionally
    creates these, and the rule methods below assume real datetime.time values.
"""
from datetime import time

from scheduler import CSPValidator


def _gene(**over):
    g = {
        "subject_code": "IT101", "class_type": "Lecture", "course": "BSIT",
        "faculty_id": None, "room_id": 1, "room_type": "Lecture",
        "day": "Monday", "days_list": ["Monday"],
        "start_time": time(7, 30), "end_time": time(9, 0),
        "duration_hrs": 1.5,
    }
    g.update(over)
    return g


def test_hc5_is_dispatched_and_flags_off_grid_time():
    csp = CSPValidator(config={})
    off_grid = _gene(start_time=time(7, 45), end_time=time(8, 15))
    violations = csp.validate([off_grid], {})
    rules = {v["rule"] for v in violations}
    assert "HC5" in rules


def test_hc5_passes_a_standard_block():
    csp = CSPValidator(config={})
    on_grid = _gene(start_time=time(7, 30), end_time=time(9, 0))
    violations = csp.validate([on_grid], {})
    assert "HC5" not in {v["rule"] for v in violations}


def test_hc5_can_be_disabled_via_config():
    csp = CSPValidator(config={"hc_time_blocks_enabled": 0})
    off_grid = _gene(start_time=time(7, 45), end_time=time(8, 15))
    violations = csp.validate([off_grid], {})
    assert "HC5" not in {v["rule"] for v in violations}


def test_hc_capacity_is_a_noop_without_class_size_data():
    # No current caller populates class_size/expected_enrollment — this must
    # never fabricate a violation just because a room_id is present.
    csp = CSPValidator(config={})
    gene = _gene(room_id=1)
    violations = csp.validate([gene], {}, rooms_by_id={1: {"roomname": "LQ101", "roomcapacity": 40}})
    assert "HC_CAPACITY" not in {v["rule"] for v in violations}


def test_hc_capacity_fires_when_size_data_is_present_and_exceeded():
    csp = CSPValidator(config={})
    gene = _gene(room_id=1, class_size=50)
    violations = csp.validate([gene], {}, rooms_by_id={1: {"roomname": "LQ101", "roomcapacity": 40}})
    assert "HC_CAPACITY" in {v["rule"] for v in violations}


def test_hc_capacity_passes_when_within_capacity():
    csp = CSPValidator(config={})
    gene = _gene(room_id=1, class_size=30)
    violations = csp.validate([gene], {}, rooms_by_id={1: {"roomname": "LQ101", "roomcapacity": 40}})
    assert "HC_CAPACITY" not in {v["rule"] for v in violations}


def test_validate_never_crashes_on_an_incomplete_gene():
    # Partial-generation path: a stripped gene has start_time/end_time/day/
    # days_list all cleared. Mixed in with a normal complete gene, validate()
    # must skip the incomplete one silently rather than raising when comparing
    # None to a real time.
    complete = _gene()
    incomplete = _gene(
        subject_code="IT102", start_time=None, end_time=None,
        day=None, days_list=[],
    )
    # Must not raise:
    violations = CSPValidator(config={}).validate([complete, incomplete], {})
    assert isinstance(violations, list)


def test_validate_room_overlap_still_catches_real_conflicts_alongside_an_incomplete_gene():
    a = _gene(subject_code="IT101", room_id=5, start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code="IT102", room_id=5, start_time=time(9, 0), end_time=time(10, 30))
    incomplete = _gene(subject_code="IT103", start_time=None, end_time=None, day=None, days_list=[])
    violations = CSPValidator(config={}).validate([a, b, incomplete], {})
    assert any(v["rule"] == "HC9" for v in violations)
