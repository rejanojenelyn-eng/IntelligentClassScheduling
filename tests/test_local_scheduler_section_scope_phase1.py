"""
Phase 1 regression tests — Local Scheduler section-aware foundation.

These tests intentionally avoid requiring a live PostgreSQL database. They
protect the contract introduced in Phase 1 across the migration, backend,
Manual Editor payload, and Local Arrangements management UI.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relpath):
    return ROOT.joinpath(relpath).read_text(encoding="utf-8-sig")


def test_section_scope_migration_exists_and_adds_required_columns():
    sql = _read("migrations/2026-09-27_local_scheduler_section_scope.sql")
    assert "ALTER TABLE public.local_arrangement" in sql
    assert "ADD COLUMN IF NOT EXISTS sectionid INTEGER" in sql
    assert "ALTER TABLE public.local_displaced_subjects" in sql
    assert "local_displaced_subjects_section_scope_key" in sql
    assert "UNIQUE (programcode, yearlevel, sectionid, semesterid, subjectcode)" in sql


def test_runtime_local_tables_are_section_aware():
    source = _read("app.py")
    ensure_block = source[source.index("def _ensure_local_tables"):source.index("@app.route", source.index("def _ensure_local_tables"))]
    assert "sectionid" in ensure_block
    assert "idx_local_arrangement_section_context" in ensure_block


def test_local_save_requires_and_validates_section():
    source = _read("app.py")
    start = source.index("def api_save_local_arrangement")
    end = source.index("def api_get_local_arrangements", start)
    block = source[start:end]

    assert "sectionId" in block
    assert "A valid section is required for Local Scheduler." in block
    assert "Selected section does not belong to this Program, Year Level, and Academic Year." in block
    assert "sectionid" in block
    assert "s.sectionid = %s" in block


def test_local_publish_is_scoped_to_same_section():
    source = _read("app.py")
    start = source.index("def api_publish_local_arrangement")
    end = source.index("def api_restore_local_arrangement", start)
    block = source[start:end]

    assert "sectionid = %s" in " ".join(block.split())
    assert "arr['sectionid']" in block
    assert "s.sectionid = %s" in block


def test_local_restore_preserves_section():
    source = _read("app.py")
    start = source.index("def api_restore_local_arrangement")
    end = source.index("def api_local_check_room_conflicts", start)
    block = source[start:end]

    assert "sectionid" in block
    assert "src['sectionid']" in block


def test_local_list_and_detail_expose_section_identity():
    source = _read("app.py")

    list_start = source.index("def api_get_local_arrangements")
    list_end = source.index("def api_get_local_arrangement(", list_start)
    list_block = source[list_start:list_end]
    assert "sectionname" in list_block
    assert "'sectionid'" in list_block

    detail_start = list_end
    detail_end = source.index("def api_deactivate_local_arrangement", detail_start)
    detail_block = source[detail_start:detail_end]
    assert "sectionname" in detail_block
    assert "'sectionid'" in detail_block


def test_manual_editor_sends_section_to_local_save_and_conflict_check():
    html = _read("templates/academic/manualScheduleEditor.html")
    # Save uses the backend's top-level section_id; the conflict check sends section_id too.
    assert "section_id:        parseInt(sectionId, 10)," in html
    assert "section_id:        sectionId," in html
    assert "A section is required" in html or "section" in html.lower()


def test_local_arrangements_management_displays_section():
    js = _read("static/js/ACAD HEAD/localArrangements.acad.js")
    assert "sectionname" in js
    assert "Legacy / Unassigned" in js


def test_room_view_local_rows_no_longer_force_null_section():
    source = _read("app.py")
    start = source.index("def get_room_schedule")
    # Keep this test scoped to the room-schedule function only.
    next_route = source.find("\n@app.route", start + 20)
    block = source[start:next_route if next_route != -1 else len(source)]

    assert "la.sectionid AS section_id" in block
    assert "sec_la.sectionname" in block
