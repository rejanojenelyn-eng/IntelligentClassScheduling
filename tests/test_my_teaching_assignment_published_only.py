from pathlib import Path
ROOT = Path(__file__).parents[1]


def test_academic_head_my_teaching_assignment_is_published_only():
    # Same rule as the Faculty page: the weekly schedule is what is in effect — no Drafts.
    for rel in ("static/js/ACAD HEAD/teachingAssign.acad.js", "static/js/FACULTY/teaching.faculty.js"):
        js = (ROOT / rel).read_text(encoding="utf-8")
        assert "prefer_draft" not in js.replace("no prefer_draft flag", ""), rel
