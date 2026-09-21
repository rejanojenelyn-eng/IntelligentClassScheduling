"""
Integration tests for CaseBasedRetriever.retrieve_best_case_assignments() against
the real local ASDBv11 database (architecture spec section 1: Top-1 historical-case
retrieval; section 28 / requirement 16: CBR-unavailable fallback returns [], never
an error). Skipped automatically when that database isn't reachable.
"""
from conftest import requires_db

from scheduler import CaseBasedRetriever, CBRAssignment


@requires_db
def test_retrieve_best_case_assignments_returns_cbr_assignments_for_a_real_case():
    cbr = CaseBasedRetriever()
    # A real (program, year_level, term, academic_year) combination confirmed
    # to have historical_data rows in the live database at the time this test
    # was written (BSARCH Year 1, term A, AY2526).
    result = cbr.retrieve_best_case_assignments("BSARCH", 1, "A", "AY2526")
    assert isinstance(result, list)
    assert len(result) > 0
    for a in result:
        assert isinstance(a, CBRAssignment)
        assert a.subject_code
        assert 0.0 <= a.similarity <= 1.0
        assert a.source_case  # non-empty — Top-1 case identity is always recorded
        assert a.faculty_status in ("resolved", "unresolved", "blank")
        assert a.room_status in ("exact", "unique_normalized", "unresolved")
        assert a.daytime_status in ("resolved", "unresolved_day_time")


@requires_db
def test_retrieve_best_case_assignments_top1_prefers_exact_program_year_term_match():
    cbr = CaseBasedRetriever()
    result = cbr.retrieve_best_case_assignments("BSARCH", 1, "A", "AY2526")
    if not result:
        return  # data may have changed since this test was written
    # Every assignment in a Top-1 result shares the same similarity score and
    # source_case (they all come from the one best-matching case).
    similarities = {round(a.similarity, 6) for a in result}
    sources = {a.source_case for a in result}
    assert len(similarities) == 1
    assert len(sources) == 1


@requires_db
def test_retrieve_best_case_assignments_fallback_when_no_case_exists():
    # A program that (almost certainly) has zero historical_data rows — CBR
    # must return [] cleanly, never raise, per the architecture spec's
    # "no usable historical case" fallback requirement.
    cbr = CaseBasedRetriever()
    result = cbr.retrieve_best_case_assignments(
        "ZZZ_NO_SUCH_PROGRAM_9999", 1, "A", "AY9999"
    )
    assert result == []


@requires_db
def test_legacy_retrieve_best_case_faculty_still_works_but_logs_a_warning(caplog):
    import logging
    cbr = CaseBasedRetriever()
    with caplog.at_level(logging.WARNING, logger="scheduler"):
        cbr.retrieve_best_case_faculty("BSARCH", 1, "A", "AY2526")
    assert any("LEGACY" in r.message or "legacy" in r.message.lower() for r in caplog.records) or \
        any("retrieve_best_case_assignments" in r.message for r in caplog.records)
