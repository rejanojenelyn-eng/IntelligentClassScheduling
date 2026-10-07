"""Local Scheduler Faculty Load: LOCAL SCHEDULE card (no Add button, no Type/Remarks
columns) above OFFICIAL TEACHING ASSIGNMENTS."""
from pathlib import Path
ROOT = Path(__file__).parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")


def test_local_card_columns_and_no_add_button():
    s = HTML.index('<div class="fl-card fl-local-card">')
    card = HTML[s:HTML.index("{% endif %}", s)]
    for col in ("SUBJECT CODE", "SUBJECT DESCRIPTION", "SECTION", "DAY", "TIME", "ROOM", "ACTIONS"):
        assert col in card
    for absent in ("TYPE", "REMARKS", "ADD LOCAL SCHEDULE"):
        assert absent not in card
    assert "{% if scheduler_mode == 'local' %}" in HTML[s - 200:s]


def test_faculty_sessions_endpoint_is_academic_head_only_and_published_active():
    s = APP.index("def api_local_faculty_sessions")
    body = APP[s:APP.index("@app.route", s)]
    assert "_require_academic_head_api()" in body
    assert "la.status = 'Published' AND la.is_active = TRUE" in body


def test_delete_warns_about_every_subject_in_the_arrangement():
    s = HTML.index("async function _flDeleteLocal(i)")
    body = HTML[s:HTML.index("\n}\n", s)]
    assert "arrangement_subjects" in body and "/deactivate" in body


def test_edit_opens_the_subject_in_the_local_editor():
    assert "subj: x.subjectcode || ''," in HTML
    assert "get('subj')" in HTML
