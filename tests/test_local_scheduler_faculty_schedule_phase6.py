from pathlib import Path
APP=Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
def block(a,b):
    s=APP.index(f"def {a}")
    e=APP.index(f"def {b}",s)
    return APP[s:e]

def test_faculty_published_view_suppresses_exact_displaced_official_occurrence():
    b=block("api_get_faculty_schedule","api_faculty_my_local_schedule")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "la_x.status = 'Published'" in b
    assert "la_x.is_active = TRUE" in b
    assert "la_x.sectionid = sc.sectionid" in b
    assert "la_x.semesterid = sc.semesterid" in b

def test_faculty_published_view_adds_active_published_local():
    b=block("api_get_faculty_schedule","api_faculty_my_local_schedule")
    assert '"la.status = \'Published\'"' in b
    assert '"la.is_active = TRUE"' in b
    assert "local_rows = query_db" in b
    assert "'Local' AS schedule_source" in b

def test_faculty_official_rows_expose_source_identity():
    b=block("api_get_faculty_schedule","api_faculty_my_local_schedule")
    assert "ss.sessionid AS official_sessionid" in b
    assert "'Official' AS schedule_source" in b

def test_faculty_draft_preferred_editor_does_not_merge_local():
    b=block("api_get_faculty_schedule","api_faculty_my_local_schedule")
    assert "if not prefer_draft:" in b
    assert "Draft-preferred mode is an Official editor view" in b

def test_faculty_local_merge_respects_requested_filters():
    b=block("api_get_faculty_schedule","api_faculty_my_local_schedule")
    assert "las.faculty_employeenumber = %s" in b
    assert "UPPER(la.programcode) = UPPER(%s)" in b
    assert "la.yearlevel = %s" in b
    assert "la.semesterid = (" in b

def test_actual_faculty_page_endpoint_returns_effective_schedule():
    b=block("api_faculty_schedule_full","api_faculty_submit_request")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "la_x.status = 'Published'" in b
    assert "la_x.is_active = TRUE" in b
    assert "'Official' AS schedule_source" in b
    assert "'Local' AS schedule_source" in b
    assert "effective = [dict(r) for r in rows] + [dict(r) for r in local_rows]" in b

def test_faculty_page_frontend_uses_single_effective_endpoint():
    js=Path(__file__).resolve().parents[1].joinpath("static/js/FACULTY/schedule.faculty.js").read_text(encoding="utf-8")
    assert "fetch('/api/faculty/schedule?'" in js
    assert "/api/faculty/my_local_schedule" not in js

def test_faculty_dashboard_today_suppresses_exact_displaced_official():
    b=block("faculty_dashboard","faculty_schedule")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "la_x.status = 'Published'" in b
    assert "la_x.is_active = TRUE" in b
    assert "'Official' AS schedule_source" in b

def test_faculty_dashboard_today_adds_active_published_local():
    b=block("faculty_dashboard","faculty_schedule")
    assert "local_today = [dict(r)" in b
    assert "las.faculty_employeenumber = %s" in b
    assert "la.status = 'Published'" in b
    assert "la.is_active = TRUE" in b
    assert "'Local' AS schedule_source" in b

def test_faculty_dashboard_load_metrics_remain_official_based():
    b=block("faculty_dashboard","faculty_schedule")
    assert "teaching-load totals below" in b
    assert "SELECT COALESCE(SUM(cs.creditunits), 0) as total_units" in b
    assert "COUNT(DISTINCT cs.subjectcode) as total_subjects" in b
    assert "sv.status = 'Published'" in b
