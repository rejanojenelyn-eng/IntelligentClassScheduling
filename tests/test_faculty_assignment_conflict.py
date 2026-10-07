from pathlib import Path
ROOT = Path(__file__).parents[1]
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")


def _fn(name):
    s = HTML.index(name)
    return HTML[s:HTML.index("\n}\n", s)]


def test_assign_button_checks_faculty_timetable_before_saving():
    body = _fn("async function assignFacultyToSubject()")
    chk = body.index("_findFacultySliceConflict(facId, facName)")
    assert chk < body.index("/api/manual/assign_faculty")
    assert "_rejectConflictingFaculty" in body


def test_faculty_pick_is_checked_before_auto_reserve_and_auto_reassign():
    s = HTML.index("window.onFacultySelect = async function(empNum)")
    body = HTML[s:HTML.index("\n};\n", s)]
    chk = body.index("_findFacultySliceConflict(empNum")
    assert chk < body.index("fetch('/api/manual/assign_faculty'")
    assert chk < body.index("confirmAllSlots({ auto: true })")


def test_conflict_check_fails_closed_and_clears_the_pick():
    body = _fn("async function _findFacultySliceConflict")
    assert "Could not verify" in body                      # lookup failure blocks
    assert "Faculty cannot have overlapping schedules." in body
    rej = _fn("async function _rejectConflictingFaculty")
    assert "document.getElementById('sel_faculty').value = ''" in rej
    assert "window._pendingFacultyAssignment = false" in rej
