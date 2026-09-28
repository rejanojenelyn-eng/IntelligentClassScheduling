"""
Phase 2 regression tests — Published Official → Local Scheduler workflow.

These characterization tests protect the rule that Local Scheduler is an
override layer over the Published Official Schedule. It may rearrange local
Day / Time / Room values, but it must not create a new subject or silently
change the Official faculty assignment.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relpath):
    return ROOT.joinpath(relpath).read_text(encoding="utf-8-sig")


def _function_block(source, function_name, next_function_name):
    start = source.index(f"def {function_name}")
    end = source.index(f"def {next_function_name}", start)
    return source[start:end]


def test_local_save_requires_published_official_source():
    source = _read("app.py")
    block = _function_block(
        source, "api_save_local_arrangement", "api_get_local_arrangements"
    )
    assert "sv.status = 'Published'" in block
    assert "Local Scheduler requires a Published Official Schedule for the selected section." in block


def test_local_save_uses_exact_section_for_official_source():
    source = _read("app.py")
    block = _function_block(
        source, "api_save_local_arrangement", "api_get_local_arrangements"
    )
    assert "s.sectionid = %s" in block
    assert "section_id" in block


def test_local_save_rejects_subject_not_in_official():
    source = _read("app.py")
    block = _function_block(
        source, "api_save_local_arrangement", "api_get_local_arrangements"
    )
    assert "official_subject_faculty" in block
    assert "is not part of the Published Official Schedule for the selected section." in block


def test_local_save_rejects_faculty_reassignment():
    source = _read("app.py")
    block = _function_block(
        source, "api_save_local_arrangement", "api_get_local_arrangements"
    )
    assert "must keep its Published Official faculty assignment" in block
    assert "requested_faculty not in allowed_faculty" in block


def test_local_save_persists_official_faculty_not_client_faculty():
    source = _read("app.py")
    block = _function_block(
        source, "api_save_local_arrangement", "api_get_local_arrangements"
    )
    assert "Never trust a Local Scheduler client payload to reassign faculty." in block
    assert "_official_faculties = official_subject_faculty.get" in block
    assert "emp_num = next((f for f in _official_faculties if f), None)" in block


def test_local_ui_locks_faculty_field():
    html = _read("templates/academic/manualScheduleEditor.html")
    assert "phase2LocalOfficialFacultyLock" in html
    assert "faculty.disabled = true" in html
    assert "OFFICIAL · LOCKED" in html
    assert "Faculty is inherited from the Published Official Schedule." in html


def test_local_ui_requires_section_and_official_subject_before_save():
    html = _read("templates/academic/manualScheduleEditor.html")
    assert "Select a section before creating a Local Arrangement." in html
    assert "Select a subject from the Published Official Schedule first." in html


def test_local_ui_explains_published_official_requirement():
    html = _read("templates/academic/manualScheduleEditor.html")
    assert "localOfficialRequirementBanner" in html
    assert "Published Official Schedule required." in html
    assert "Local Scheduler can only rearrange subjects from the Published Official Schedule" in html


def test_phase1_section_contract_is_still_present():
    """Phase 2 must not regress the section-aware foundation from Phase 1."""
    source = _read("app.py")
    block = _function_block(
        source, "api_save_local_arrangement", "api_get_local_arrangements"
    )
    assert "sectionId" in block
    assert "A valid section is required for Local Scheduler." in block
    assert "Selected section does not belong to this Program, Year Level, and Academic Year." in block
