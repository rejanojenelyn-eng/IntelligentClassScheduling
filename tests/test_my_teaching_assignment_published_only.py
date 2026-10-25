from pathlib import Path
ROOT = Path(__file__).parents[1]


def test_academic_head_my_teaching_assignment_is_published_only():
    # Same rule as the Faculty page: the weekly schedule is what is in effect — no Drafts.
    js = (ROOT / "static/js/FACULTY/teaching.faculty.js").read_text(encoding="utf-8")
    assert "prefer_draft" not in js.replace("no prefer_draft flag", "")


def test_academic_head_page_uses_the_faculty_script():
    # The Academic Head's own "My Teaching Assignment" mirrors the Faculty page exactly.
    html = (ROOT / "templates/academic/AcadTeachingAssign.html").read_text(encoding="utf-8")
    assert "js/FACULTY/teaching.faculty.js" in html
    assert '{% extends "academic/base.html" %}' in html
