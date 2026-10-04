"""
Unit tests for the fixes applied per the "Schedule Generation Bug Analysis":
  1/2/3/4  historical time parsing (scheduler._parse_historical_time_verbose)
  5        adjacent schedules must not be flagged as overlapping (HC10/HC11/HC12
           final ids -- were HC10/HC9/HC11 before the Phase B checkpoint 2
           renumbering)
  6/7      HC12 (final; was HC11) section-overlap now requires the same
           normalized section
  8        one violation per unique conflicting pair, not one per shared day
  9        compound-day normalization (scheduler.normalize_day_tokens)
  10       structured violations carry affected_components for cell placement
  11/12    generate_draft(use_historical=True) repairs before failing outright,
           and degrades to PARTIAL_VALID rather than INVALID_RESULT when a
           violation IS safely narrowed to one component
  15       exact-reuse success/failure is reported explicitly, never silent

Items 13/14 (INVALID_RESULT never saved as a normal Draft; an Incomplete
Draft can never be published) exercise pre-existing, already-implemented
gates in app.py (the pre-save CSP check ahead of _insert_batch, and the
sv.is_incomplete filter in /api/schedule/approve) that require the real
Postgres database — see test_incomplete_draft_persistence.py for that
pattern. They are not duplicated here; this file covers only what can run
without a database (see conftest.py's DB_AVAILABLE / requires_db split).
"""
from datetime import time

from scheduler import (
    CSPValidator,
    IntelligentScheduler,
    _parse_historical_time,
    _parse_historical_time_verbose,
    parse_historical_day_time,
    parse_historical_day_time_verbose,
    normalize_day_tokens,
)


def _gene(**over):
    g = {
        "subject_code": "IT101", "class_type": "Lecture", "course": "BSIT",
        "faculty_id": None, "instructor": "Dela Cruz, J.",
        "room_id": 1, "room": "LQ101", "room_type": "Lecture",
        "day": "Monday", "days_list": ["Monday"],
        "start_time": time(7, 30), "end_time": time(9, 0),
        "duration_hrs": 1.5,
    }
    g.update(over)
    return g


# ── 1/2/3/4: historical time parsing ──────────────────────────────────────

def test_both_sided_am_pm_resolves_correctly():
    cases = {
        "3:00 PM-6:00 PM":   (time(15, 0), time(18, 0)),
        "3:00 PM - 6:00 PM": (time(15, 0), time(18, 0)),
        "9:00 AM-12:00 PM":  (time(9, 0),  time(12, 0)),
        "7:30 AM-10:30 AM":  (time(7, 30), time(10, 30)),
        "10:30 AM-1:30 PM":  (time(10, 30), time(13, 30)),
        "5:00 PM-7:00 PM":   (time(17, 0), time(19, 0)),
    }
    for raw, expected in cases.items():
        start, end, reason = _parse_historical_time_verbose(raw)
        assert (start, end) == expected, f"{raw!r} -> {(start, end)}, reason={reason}"
        assert reason is None


def test_trailing_meridiem_only_applies_to_both_sides():
    start, end, reason = _parse_historical_time_verbose("3:00-6:00 PM")
    assert (start, end) == (time(15, 0), time(18, 0))
    assert reason is None


def test_midnight_and_noon_boundaries():
    start, end, reason = _parse_historical_time_verbose("12:00 AM-3:00 AM")
    assert (start, end) == (time(0, 0), time(3, 0))
    assert reason is None

    start, end, reason = _parse_historical_time_verbose("12:00 PM-3:00 PM")
    assert (start, end) == (time(12, 0), time(15, 0))
    assert reason is None


def test_24_hour_historical_values_are_unambiguous():
    for raw, expected in {
        "15:00-18:00":          (time(15, 0), time(18, 0)),
        "15:00:00 - 18:00:00":  (time(15, 0), time(18, 0)),
    }.items():
        start, end, reason = _parse_historical_time_verbose(raw)
        assert (start, end) == expected, raw
        assert reason is None


def test_genuinely_ambiguous_bare_time_without_a_block_match_is_unresolved():
    # No meridiem on either side, both hours 1-12, and not a real STANDARD_BLOCKS
    # pair in either AM/PM interpretation — must be rejected, never guessed.
    start, end, reason = _parse_historical_time_verbose("3:17-6:42")
    assert start is None and end is None
    assert reason is not None  # a reason is always given, never a silent None


def test_multi_block_and_blank_are_rejected_with_a_reason():
    assert _parse_historical_time_verbose("7:30-12:00/ 12:30-5:00")[2] == "multi_block"
    assert _parse_historical_time_verbose("")[2] == "blank"
    assert _parse_historical_time_verbose("TBA")[2] == "blank"


def test_end_before_start_is_rejected():
    start, end, reason = _parse_historical_time_verbose("6:00 PM-3:00 PM")
    assert start is None and end is None
    assert reason == "end_before_start"


def test_grid_gated_wrapper_still_rejects_off_grid_time_regardless_of_am_pm():
    # "3:00 PM-6:00 PM" is unambiguous, but this system's STANDARD_BLOCKS grid
    # has no single block spanning exactly 15:00-18:00 (it pivots at 16:30) —
    # _parse_historical_time (used by CBR locking) must still reject it exactly
    # as it did before this fix, since HC5 checks start/end independently and
    # would NOT catch an off-grid pair on its own (see the module docstring on
    # _parse_historical_time). The verbose/display-oriented function is
    # unaffected — it correctly resolves the same string.
    assert _parse_historical_time("3:00 PM-6:00 PM") is None
    start, end, reason = _parse_historical_time_verbose("3:00 PM-6:00 PM")
    assert (start, end) == (time(15, 0), time(18, 0))
    assert reason is None


def test_parse_historical_day_time_verbose_resolves_off_grid_times_for_display():
    days, start, end, status, reason = parse_historical_day_time_verbose("MW", "3:00 PM-6:00 PM")
    assert status == "resolved" and reason is None
    assert days == ["Monday", "Wednesday"]
    assert (start, end) == (time(15, 0), time(18, 0))

    # The grid-gated CBR variant must still refuse to lock this as a placeable slot.
    days2, start2, end2, status2 = parse_historical_day_time("MW", "3:00 PM-6:00 PM")
    assert status2 != "resolved"
    assert start2 is None and end2 is None


# ── 5: adjacent schedules must not overlap ────────────────────────────────

def test_adjacent_room_schedules_do_not_conflict():
    a = _gene(subject_code="IT101", room_id=5, start_time=time(15, 0), end_time=time(18, 0))
    b = _gene(subject_code="IT102", room_id=5, start_time=time(18, 0), end_time=time(21, 0))
    violations = CSPValidator(config={}).validate([a, b], {})
    assert "HC11" not in {v["rule"] for v in violations}


def test_adjacent_faculty_schedules_do_not_conflict():
    a = _gene(subject_code="IT101", faculty_id="E001", start_time=time(15, 0), end_time=time(18, 0))
    b = _gene(subject_code="IT102", faculty_id="E001", start_time=time(18, 0), end_time=time(21, 0))
    violations = CSPValidator(config={}).validate([a, b], {})
    assert "HC10" not in {v["rule"] for v in violations}


# ── 6/7: HC12 (final; was HC11) requires the same normalized section ──────

def test_hc12_fires_within_the_same_section():
    a = _gene(subject_code="IT101", start_time=time(9, 0), end_time=time(10, 30),
              course="BSIT", section_name="1A")
    b = _gene(subject_code="IT102", start_time=time(9, 0), end_time=time(10, 30),
              course="BSIT", section_name="1A")
    violations = CSPValidator(config={}).validate([a, b], {})
    assert "HC12" in {v["rule"] for v in violations}


def test_hc12_does_not_fire_across_different_sections():
    a = _gene(subject_code="IT101", start_time=time(9, 0), end_time=time(10, 30),
              course="BSIT", section_name="1A")
    b = _gene(subject_code="IT102", start_time=time(9, 0), end_time=time(10, 30),
              course="BSIT", section_name="1B")
    violations = CSPValidator(config={}).validate([a, b], {})
    assert "HC12" not in {v["rule"] for v in violations}


def test_hc12_still_fires_when_section_identity_is_absent_on_both_sides():
    # A normal single-section generate_draft run never stamps section_name on
    # its genes at all — the whole validate() call is already implicitly
    # scoped to one section. Unknown-on-both-sides must default to "same
    # section" so this common case doesn't silently stop being checked.
    a = _gene(subject_code="IT101", start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code="IT102", start_time=time(9, 0), end_time=time(10, 30))
    violations = CSPValidator(config={}).validate([a, b], {})
    assert "HC12" in {v["rule"] for v in violations}


# ── 8: one violation per unique pair, with all shared days preserved ──────

def test_one_violation_per_pair_not_per_shared_day():
    a = _gene(subject_code="IT101", room_id=5, days_list=["Monday", "Wednesday"],
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code="IT102", room_id=5, days_list=["Monday", "Wednesday"],
              start_time=time(9, 0), end_time=time(10, 30))
    violations = CSPValidator(config={}).validate([a, b], {})
    room_viols = [v for v in violations if v["rule"] == "HC11"]
    assert len(room_viols) == 1
    assert set(room_viols[0]["normalized_days"]) == {"Monday", "Wednesday"}


def test_a_vs_b_and_b_vs_a_are_not_double_counted():
    a = _gene(subject_code="IT101", faculty_id="E001", start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code="IT102", faculty_id="E001", start_time=time(9, 0), end_time=time(10, 30))
    c = _gene(subject_code="IT103", faculty_id="E001", start_time=time(9, 0), end_time=time(10, 30))
    violations = CSPValidator(config={}).validate([a, b, c], {})
    # 3 genes, same faculty, all mutually overlapping -> exactly 3 unique pairs
    assert len([v for v in violations if v["rule"] == "HC10"]) == 3


# ── 9: compound-day normalization ─────────────────────────────────────────

def test_normalize_day_tokens_handles_single_and_compound_tokens():
    assert normalize_day_tokens("M") == ["Monday"]
    assert normalize_day_tokens("MON") == ["Monday"]
    assert normalize_day_tokens("TH") == ["Thursday"]
    assert normalize_day_tokens("MW") == ["Monday", "Wednesday"]
    assert normalize_day_tokens("TTH") == ["Tuesday", "Thursday"]
    assert normalize_day_tokens("MTH") == ["Monday", "Thursday"]


def test_app_parse_days_delegates_to_the_canonical_normalizer():
    # Imported lazily (app.py has a heavy import surface) — this only checks
    # that the app-level day parser now expands compound tokens identically
    # to the scheduler's canonical one, instead of its own weaker map.
    import app
    assert app._parse_days("MW") == ["Monday", "Wednesday"]
    assert app._parse_days("TTH") == ["Tuesday", "Thursday"]
    assert app._parse_days("MTH") == ["Monday", "Thursday"]
    # Already-full day names must still round-trip unchanged (backward compat).
    assert app._parse_days("Monday/Wednesday") == ["Monday", "Wednesday"]


# ── 10: structured violations carry affected_components ──────────────────

def test_room_overlap_violation_has_structured_fields_for_cell_placement():
    a = _gene(subject_code="IT101", room_id=5, faculty_id="E001",
              start_time=time(9, 0), end_time=time(10, 30))
    b = _gene(subject_code="IT102", room_id=5, faculty_id="E002",
              start_time=time(9, 0), end_time=time(10, 30))
    violations = CSPValidator(config={}).validate([a, b], {})
    room_v = next(v for v in violations if v["rule"] == "HC11")
    assert set(room_v["affected_components"]) == {"room", "day", "time"}
    assert room_v["room_id"] == 5
    assert room_v["rule_name"]
    assert "start_time" in room_v and "end_time" in room_v


def test_every_rule_gets_a_default_affected_components_via_label_violation():
    # HC5 (Restricted-Day Subject Requirement -- final id; was internal HC4)
    # is a single-gene check with no pairwise builder — confirms the central
    # _label_violation fallback covers it too.
    from scheduler import _label_violation
    v = _label_violation({"rule": "HC5", "subject": "NSTP101", "detail": "x"})
    assert v["affected_components"] == ["day"]
    assert v["rule_name"] == v["type"]


# ── 11/12: use_historical repair-before-failing + explicit reuse reporting ─

def _make_scheduler_with_fake_history(monkeypatch, hist_rows, violations_to_return):
    sch = IntelligentScheduler()

    def _fake_fetch_data(program, year_level, term, curriculum):
        return (
            [{"subjectcode": "IT101"}],           # subjects (non-empty)
            [{"employeenumber": "E001"}],          # faculty_list (non-empty)
            {"E001": {"employeenumber": "E001"}},  # faculty_map
            [{"roomid": 1, "roomname": "LQ101"}],  # rooms (non-empty)
        )
    monkeypatch.setattr(sch, "fetch_data", _fake_fetch_data)
    monkeypatch.setattr(sch, "fetch_historical_schedule", lambda *a, **kw: hist_rows)

    # generate_draft reassigns self.csp = CSPValidator(...) at the very top of
    # every call (by design — see its own comment: "Reload constraint config on
    # every call so admin Settings changes take effect immediately"), so an
    # instance-level patch made before calling it would be orphaned. Patch the
    # CLASS method instead, which survives that reassignment.
    calls = {"n": 0}

    def _fake_validate(self, schedule, faculty_map, rooms_by_id=None, **kw):
        calls["n"] += 1
        return violations_to_return if calls["n"] == 1 else []
    monkeypatch.setattr(CSPValidator, "validate", _fake_validate)
    return sch


def test_use_historical_repairs_a_narrowable_violation_into_partial_valid(monkeypatch):
    hist_rows = [_gene(subject_code="IT101", room_id=1, faculty_id="E001")]
    violation = {
        "rule": "HC11", "subject": "IT101", "detail": "room conflict (test)",
        "severity": None,
    }
    sch = _make_scheduler_with_fake_history(monkeypatch, hist_rows, [violation])
    result = sch.generate_draft("BSIT", 1, "A", "2025", use_historical=True)

    assert result["exact_reuse_requested"] is True
    assert result["result_status"] == "PARTIAL_VALID"
    assert result["incomplete_count"] == 1
    assert result["schedule_data"][0]["room_id"] is None  # the room component was stripped
    assert result["schedule_data"][0]["faculty_id"] == "E001"  # untouched


def test_use_historical_returns_complete_valid_when_no_violations(monkeypatch):
    hist_rows = [_gene(subject_code="IT101", room_id=1, faculty_id="E001")]
    sch = _make_scheduler_with_fake_history(monkeypatch, hist_rows, [])
    result = sch.generate_draft("BSIT", 1, "A", "2025", use_historical=True)

    assert result["result_status"] == "COMPLETE_VALID"
    assert result["exact_reuse_succeeded"] is True
    assert result["incomplete_count"] == 0


def test_use_historical_stays_invalid_when_violation_cannot_be_narrowed(monkeypatch):
    hist_rows = [_gene(subject_code="IT101", room_id=1, faculty_id="E001")]
    # 'MULTIPLE' subject field -> can't be safely narrowed to one gene (mirrors
    # the normal generation path's own unresolved_hard_violations rule).
    violation = {"rule": "HC9", "subject": "MULTIPLE", "detail": "aggregate load issue"}
    sch = _make_scheduler_with_fake_history(monkeypatch, hist_rows, [violation])
    result = sch.generate_draft("BSIT", 1, "A", "2025", use_historical=True)

    assert result["result_status"] == "INVALID_RESULT"
    assert result["exact_reuse_succeeded"] is False
    assert result["conflict_count"] == 1


# ── 15: exact-reuse unavailability is reported, never silent ─────────────

def test_use_historical_does_not_crash_when_no_source_schedule_exists(monkeypatch):
    # fetch_historical_schedule returning None (no Published/Draft source —
    # e.g. archived) must not raise, and generation must still return a
    # well-formed result rather than silently pretending exact reuse
    # succeeded. Full verification that 'exact_reuse_unavailable_reason'
    # reaches the final response requires running the complete GA pipeline
    # (fetch_data returning real subjects/faculty/rooms) end-to-end against a
    # real database — see test_generation_integration.py's @requires_db
    # pattern; that field is exercised directly at the API layer instead in
    # /api/schedule/retrieve-previous, which is the actual reachable caller
    # (generate_draft's own use_historical branch has no current production
    # caller — see the implementation report).
    sch = IntelligentScheduler()

    def _fake_fetch_data(program, year_level, term, curriculum):
        return ([], [], {}, [])  # no subjects -> GENERATION_ERROR, by design
    monkeypatch.setattr(sch, "fetch_data", _fake_fetch_data)
    monkeypatch.setattr(sch, "fetch_historical_schedule", lambda *a, **kw: None)

    result = sch.generate_draft("BSIT", 1, "A", "2025", use_historical=True)
    assert result["result_status"] == "GENERATION_ERROR"
    assert result["success"] is False
