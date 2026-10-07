"""Once a subject+section has a pending Draft (e.g. a regenerated schedule that overrode
it), the editor shows that Draft everywhere: no Published relabeling of identical slices,
no superseded Published bars in Program View, and the curriculum badge reads DRAFT."""
from pathlib import Path
ROOT = Path(__file__).parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")


def test_existing_sessions_does_not_relabel_draft_slices_as_published():
    s = APP.index("def api_manual_existing_sessions") if "def api_manual_existing_sessions" in APP \
        else APP.index("@app.route('/api/manual/existing_sessions')")
    body = APP[s:APP.index("@app.route", s + 40)]
    assert "pub_slot_keys" not in body
    assert "rows = [dict(r) for r in draft_rows]" in body


def test_program_view_hides_published_rows_superseded_by_a_draft():
    s = APP.index("def get_offerings_schedule")
    body = APP[s:APP.index("@app.route", s + 40)]
    assert "if version_status == 'active':" in body
    assert "(str(r['subjectcode']).upper(), r['section_id']) in _drafted" in body


def test_curriculum_badge_shows_draft_for_pending_draft():
    assert "badgeText = 'PUB/DRAFT'" not in HTML and "bt = 'PUB/DRAFT'" not in HTML
    assert "Pending Draft — the current Published schedule stays live" in HTML
