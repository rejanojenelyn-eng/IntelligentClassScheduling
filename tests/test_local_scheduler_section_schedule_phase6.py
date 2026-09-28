from pathlib import Path

APP=Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

def block(a,b):
    s=APP.index(f"def {a}")
    e=APP.index(f"def {b}",s)
    return APP[s:e]

def test_official_mode_keeps_published_and_draft_without_local_merge():
    b=block("api_manual_section_schedule","api_get_valid_semesters")
    assert "scheduler_mode != 'local'" in b
    assert "sv.status IN ('Published', 'Draft')" in b

def test_local_mode_uses_published_official_only():
    b=block("api_manual_section_schedule","api_get_valid_semesters")
    assert "sv.status = 'Published'" in b
    assert "official_rows = query_db" in b

def test_local_mode_suppresses_only_exact_displaced_occurrence():
    b=block("api_manual_section_schedule","api_get_valid_semesters")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "la_x.sectionid = sc.sectionid" in b
    assert "la_x.semesterid = sc.semesterid" in b
    assert "la_x.status = 'Published'" in b
    assert "la_x.is_active = TRUE" in b

def test_local_mode_adds_only_active_published_local_rows():
    b=block("api_manual_section_schedule","api_get_valid_semesters")
    assert '"la.status = \'Published\'"' in b
    assert '"la.is_active = TRUE"' in b
    assert "'Local' AS schedule_source" in b

def test_section_schedule_exposes_source_and_occurrence_identity():
    b=block("api_manual_section_schedule","api_get_valid_semesters")
    assert "ss.sessionid AS official_sessionid" in b
    assert "'Official' AS schedule_source" in b
    assert "las.official_sessionid" in b

def test_local_rows_respect_same_program_year_period_and_section_filters():
    b=block("api_manual_section_schedule","api_get_valid_semesters")
    assert '"UPPER(la.programcode) = UPPER(%s)"' in b
    assert '"la.yearlevel::text = %s"' in b
    assert '"ay.academicyearid::text = %s"' in b
    assert '"s.semestertype = %s"' in b
    assert 'local_filters.append("la.sectionid = %s")' in b

def test_program_view_local_mode_uses_exact_occurrence_displacement():
    b=block("get_offerings_schedule","debug_hist")
    local=b[:b.index("# ── Determine data source:")]
    assert "las.official_sessionid = ss.sessionid" in local
    assert "la.status = 'Published'" in local
    assert "la.is_active = TRUE" in local
    assert "UPPER(las.subjectcode) = UPPER(cs.subjectcode)" not in local

def test_program_view_local_rows_keep_section_and_source_identity():
    b=block("get_offerings_schedule","debug_hist")
    local=b[:b.index("# ── Determine data source:")]
    assert "sec2.sectionname" in local
    assert "la.sectionid" in local
    assert "las.official_sessionid" in local
    assert "'Local'" in local and "schedule_source" in local
    assert "'Official'" in local and "schedule_source" in local

def test_program_view_local_side_respects_section_and_faculty_filters():
    b=block("get_offerings_schedule","debug_hist")
    local=b[:b.index("# ── Determine data source:")]
    assert "local_extra_clause += ' AND la.sectionid = %s'" in local
    assert "local_extra_clause += ' AND las.faculty_employeenumber = %s'" in local

def test_admin_sis_page_remains_official_institutional_view():
    root=Path(__file__).resolve().parents[1]
    html=root.joinpath("templates/admin/class_schedule_sis_admin.html").read_text(encoding="utf-8")
    js=root.joinpath("static/js/ACAD HEAD/schedule.acad.js").read_text(encoding="utf-8")
    assert "Official and Approved Schedule in the PUP Student Information System (SIS)" in html
    assert "scheduler_mode=local" not in js
