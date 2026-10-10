from pathlib import Path
ROOT = Path(__file__).parents[1]
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")


def test_reassignment_records_old_faculty_and_status():
    s = HTML.index("window.unlockFacultyChoice = async function()")
    body = HTML[s:HTML.index("\n};\n", s)]
    assert "window._facultyReassignInfo = code ? {" in body
    assert "fromName:" in body and "wasPublished," in body


def test_auto_save_explains_the_faculty_change_instead_of_generic_draft_notice():
    assert "if (!_reassign) await showValidationModal('Saved as Draft'" in HTML
    assert "was changed from ${_reassign.fromName} to ${_reassign.toName}" in HTML


def test_published_stays_published_and_draft_stays_draft():
    assert "_runManualApprove({ auto: true, reasonLine: _reasonLine })" in HTML
    assert "Status: Published (unchanged)." in JS
    assert "Status: Draft (unchanged)." in HTML
    # a failed republish is reported, never silent
    assert "Faculty Changed — Not Yet Republished" in HTML


def test_reassign_info_is_always_consumed():
    s = HTML.index("window._facultyReassignInfo.toName = _selFacName")
    assert "window._facultyReassignInfo = null;   // never leaks" in HTML[s:s + 1500]
