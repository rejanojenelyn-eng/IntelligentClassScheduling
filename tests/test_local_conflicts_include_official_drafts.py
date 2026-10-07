"""Local Scheduler must not place a faculty where their pending Official Draft already
sits: approving that Draft would double-book them (and the Official Scheduler already
refuses that slot)."""
from pathlib import Path
ROOT = Path(__file__).parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")


def _local_check():
    return APP[APP.index("def api_local_check_room_conflicts"):APP.index("@app.route('/api/get_room_schedule")]


def test_local_check_queries_the_faculty_official_drafts():
    body = _local_check()
    assert "'Official Draft' AS source" in body
    assert "sv.status='Draft' AND sv.source IS DISTINCT FROM 'local'" in body
    assert "COALESCE(sv.employeenumber, s.employeenumber)=%s" in body


def test_own_subject_draft_in_this_section_is_not_a_conflict():
    assert "AND NOT (s.sectionid=%s AND UPPER(cs.subjectcode)=%s)" in _local_check()


def test_conflict_modal_explains_official_draft_rows():
    assert "c.source === 'Official Draft'" in HTML


def test_local_publish_also_blocks_faculty_vs_official_draft():
    s = APP.index("@app.route('/api/local/arrangement/<int:arr_id>/publish'")
    body = APP[s:APP.index("@app.route", s + 40)]
    assert "'source':'Official Draft'" in body
    assert "AND sv.source IS DISTINCT FROM 'local'" in body
    assert "if d == 'Faculty']" in body          # a Draft reserves no room/section


def test_no_day_recommendations_in_local_scheduler():
    s = HTML.index("function _renderDayRecommendations(rowId)")
    body = HTML[s:HTML.index("\n}\n", s)]
    assert "(typeof IS_LOCAL !== 'undefined' && IS_LOCAL) ? [] :" in body
