"""
Phase 3 regression tests — Local Arrangement Draft/Publish/Archive/Restore lifecycle.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _read(relpath):
    return ROOT.joinpath(relpath).read_text(encoding="utf-8-sig")

def _block(source, start_name, end_name):
    s = source.index(f"def {start_name}")
    e = source.index(f"def {end_name}", s)
    return source[s:e]

def test_publish_requires_reason():
    src = _read("app.py")
    b = _block(src, "api_publish_local_arrangement", "api_restore_local_arrangement")
    assert "A reason is required before publishing a Local Arrangement." in b
    assert "publish_reason" in b

def test_only_draft_can_publish():
    src = _read("app.py")
    b = _block(src, "api_publish_local_arrangement", "api_restore_local_arrangement")
    assert "Only Draft Local Arrangements can be published." in b
    assert "status') or '').strip().lower() != 'draft'" in b

def test_publish_revalidates_published_official():
    src = _read("app.py")
    b = _block(src, "api_publish_local_arrangement", "api_restore_local_arrangement")
    assert "sv.status = 'Published'" in b
    assert "s.sectionid = %s" in b
    assert "no longer has a Published Official Schedule" in b

def test_publish_archives_only_same_section_context():
    src = _read("app.py")
    b = _block(src, "api_publish_local_arrangement", "api_restore_local_arrangement")
    assert "sectionid = %s" in b
    assert "status = 'Archived'" in b
    assert "arrangementid <> %s" in b

def test_publish_stores_reason_and_official_reference():
    src = _read("app.py")
    b = _block(src, "api_publish_local_arrangement", "api_restore_local_arrangement")
    assert "ref_versionid = %s" in b
    assert "reason = %s" in b
    assert "latest_official_versionid, publish_reason, arr_id" in b

def test_restore_is_new_draft_not_in_place_edit():
    src = _read("app.py")
    b = _block(src, "api_restore_local_arrangement", "api_local_check_room_conflicts")
    assert "new editable Draft" in b
    assert "sectionid" in b
    assert "'Draft'" in b or '"Draft"' in b

def test_editor_publish_sends_reason_json():
    html = _read("templates/academic/manualScheduleEditor.html")
    assert "async function publishLocalArrangement(arrId, btn)" in html
    assert "_publishLocalArrangementRequest(arrId, cleanReason)" in html
    assert "expected_updated_at: _localArrUpdatedAt[String(arrId)] || null," in html
    # The reason is optional for the user; a blank one gets an automatic audit reason.
    assert "|| 'Published from Version History'" in html

def test_editor_publish_explains_archive_behavior():
    html = _read("templates/academic/manualScheduleEditor.html")
    assert "previous Published Local Arrangement" in html
    assert "will be archived" in html

def test_editor_has_explicit_publish_from_editor_flow():
    html = _read("templates/academic/manualScheduleEditor.html")
    assert "publishLocalArrangementFromEditor" in html
    assert "SAVE AS LOCAL ARRANGEMENT" in html

def test_restore_as_draft_wording_is_owned_by_version_history_ui():
    html = _read("templates/academic/manualScheduleEditor.html")
    assert "Restore as Draft" in html
