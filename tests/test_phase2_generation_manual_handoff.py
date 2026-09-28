from pathlib import Path
ROOT=Path(__file__).parents[1]
GEN=(ROOT/"static/js/ACAD HEAD/scheduleGeneration.acad.js").read_text(encoding="utf-8")
HTML=(ROOT/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
APP=(ROOT/"app.py").read_text(encoding="utf-8")

def test_backend_exposes_unsaved_incomplete_subjects():
    assert "'unsaved_incomplete_subjects': sorted(unsaved_incomplete)" in APP

def test_complete_success_does_not_transfer_full_snapshot():
    block=GEN[GEN.index("// Handoff rule:"):GEN.index("btnManualEditor.innerHTML", GEN.index("// Handoff rule:"))]
    assert "if (_savedAsDraft)" in block
    assert "unsaved_incomplete_subjects" in block
    assert "mode:          'unsaved_only'" in block
    # Full currentScheduleData is used only in the failure branch.
    assert "mode:          'full_fallback'" in block

def test_partial_success_transfers_only_unsaved_subject_codes():
    assert "const _unsavedRows = (currentScheduleData || []).filter" in GEN
    assert "_unsavedCodes.has(code)" in GEN
    assert "schedule_data: _unsavedRows" in GEN

def test_failed_save_keeps_full_fallback():
    assert "mode:          'full_fallback'" in GEN
    assert "schedule_data: currentScheduleData" in GEN

def test_manual_has_single_generator_normalizer():
    assert "function _normalizeGeneratorTransferEntry" in HTML
    for alias in ["e.days_list ?? e.days ?? e.daydesc ?? e.day", "e.RoomID", "e.start_time_id", "e.end_time_id"]:
        assert alias in HTML

def test_fully_unresolved_subject_is_not_dropped():
    assert "const days = n.days.length ? n.days : [''];" in HTML
    assert "isIncompleteFallback: transfer.mode === 'unsaved_only'" in HTML

def test_transfer_is_consumed_once_and_deduplicated():
    assert "sessionStorage.removeItem('_sched_gen_transfer')" in HTML
    assert "function _generatorPendingKey" in HTML
    assert "if (!existingKeys.has(key)" in HTML

def test_section_identity_is_preserved():
    assert "ctx.section || ctx.sectionId || ctx.section_id || e.section_id || e.sectionid" in HTML
