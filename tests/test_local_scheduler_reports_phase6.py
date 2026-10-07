from pathlib import Path
APP=Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

def block(a,b):
    s=APP.index(f"def {a}")
    e=APP.index(b,s)
    return APP[s:e]

def test_class_export_published_official_uses_exact_displacement():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "la_x.status = 'Published'" in b
    assert "la_x.is_active = TRUE" in b
    assert "la_x.sectionid = sc.sectionid" in b
    assert "la_x.semesterid = sc.semesterid" in b

def test_class_export_preserves_official_draft_preview():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert "sv.status IN ('Published', 'Draft')" in b
    assert "sv.status = 'Draft'" in b

def test_class_export_adds_only_active_published_local():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert 'lf, lp_ = ["la.status = \'Published\'", "la.is_active = TRUE"]' in b
    assert "'Local' AS \"ScheduleSource\"" in b

def test_class_export_local_respects_report_filters():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert "ay_l.academicyearid IN" in b
    assert "sem_l.semestertype IN" in b
    assert "UPPER(la.programcode) IN" in b
    assert "la.yearlevel IN" in b

def test_historical_fallback_uses_effective_current_rows():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert "current_rows = norm_rows + local_rows" in b
    assert "hist_extra" in b
    assert "'Historical' AS \"ScheduleSource\"" in b

def test_merge_happens_after_effective_resolution():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert "combined = norm_timed + hist_extra + norm_untimed" in b
    assert "return _sch_merge_session_rows(combined) if merge else combined" in b

import faculty_load as _fl


def test_room_report_uses_exact_occurrence_displacement():
    # Room occupancy reads the ONE shared effective-schedule definition.
    b=block("_room_report_fetch","def _room_report_groups")
    assert "faculty_load.EFFECTIVE_SESSIONS_CTE" in b
    cte=_fl.EFFECTIVE_SESSIONS_CTE
    assert "las_x.official_sessionid = ss.sessionid" in cte
    assert "la_x.status = 'Published' AND la_x.is_active = TRUE" in cte

def test_room_report_is_live_occupancy_without_drafts():
    # Pending Drafts are not room occupancy (Phase 9.3).
    b=block("_room_report_fetch","def _room_report_groups")
    assert "'Draft'" not in b
    assert "sv.status = 'Published'" in _fl.EFFECTIVE_SESSIONS_CTE
    assert "'Draft'" not in _fl.EFFECTIVE_SESSIONS_CTE

def test_room_report_adds_only_active_published_local():
    cte=_fl.EFFECTIVE_SESSIONS_CTE
    assert "WHERE la.status = 'Published' AND la.is_active = TRUE" in cte
    assert 'es.source AS "ScheduleSource"' in block("_room_report_fetch","def _room_report_groups")

def test_room_report_filters_apply_to_both_sources():
    b=block("_room_report_fetch","def _room_report_groups")
    for f in ("sem.academicyearid IN", "sem.semestertype IN", "b.buildingid = %s",
              "r.roomtype = %s", "r.roomid = %s"):
        assert f in b

def test_admin_room_report_data_uses_the_same_live_rows():
    b=block("_fetch_report_data","# ─── Report title mapping")
    start=b.index("elif report_type == 'room_schedule':")
    assert "_room_report_fetch(cur" in b[start:start+600]

def test_teaching_assignment_report_remains_official_assignment_based():
    b=block("_fetch_report_data","# ─── Report title mapping")
    start=b.index("elif report_type == 'assignments':")
    end=b.index("# ── ACADEMIC OFFERINGS", start)
    a=b[start:end]
    assert "FROM schedule_version sv" in a
    assert "JOIN schedule sg" in a
    assert "local_arrangement" not in a
    assert "(cs.lecturehours+cs.laboratoryhours)" in a

def test_teaching_assignment_report_preserves_version_history_scope():
    b=block("_fetch_report_data","# ─── Report title mapping")
    start=b.index("elif report_type == 'assignments':")
    end=b.index("# ── ACADEMIC OFFERINGS", start)
    a=b[start:end]
    assert "sv.status IN ('Published','Archive','Draft')" in a

def test_class_schedule_exports_share_effective_schedule_fetch():
    b=block("report_export","# ==============================================================================")
    assert "if report_type == 'class_schedule':" in b
    assert "_sch_export_context" in b
    # _sch_export_context ultimately consumes _sch_exp_fetch, already protected by 6.5-A.
    app=APP
    assert "def _sch_export_context" in app
    assert "def _sch_exp_fetch" in app

def test_no_separate_faculty_timetable_report_branch_can_bypass_effective_class_export():
    b=block("_fetch_report_data","# ─── Report title mapping")
    assert "report_type == 'faculty_schedule'" not in b
    assert "report_type == 'faculty_timetable'" not in b

def test_class_export_historical_query_does_not_join_local_scheduler():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    hs=b.index("hist_q =")
    he=b.index("cur.execute(hist_q", hs)
    hist=b[hs:he]
    assert "FROM historical_data hd" in hist
    assert "local_arrangement" not in hist
    assert "'Historical' AS \"ScheduleSource\"" in hist

def test_historical_fallback_is_added_after_effective_current_resolution():
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert b.index("current_rows = norm_rows + local_rows") < b.index("hist_extra")
    assert "hist_extra     = [r for r in hist_rows if r['SubjectCode'] not in norm_with_time]" in b

def test_academic_offerings_history_remains_official_version_history():
    b=block("_fetch_report_data","# ─── Report title mapping")
    start=b.index("elif report_type == 'offerings':")
    end=b.index("# ── PROGRAM LIST", start)
    o=b[start:end]
    assert "sv.status IN ('Published','Archive','Draft')" in o
    assert "FROM schedule_version sv" in o
    assert "local_arrangement" not in o

def test_teaching_assignment_archive_history_remains_official_only():
    b=block("_fetch_report_data","# ─── Report title mapping")
    start=b.index("elif report_type == 'assignments':")
    end=b.index("# ── ACADEMIC OFFERINGS", start)
    a=b[start:end]
    assert "sv.status IN ('Published','Archive','Draft')" in a
    assert "local_arrangement" not in a

def test_local_rows_never_enter_historical_data_table():
    # Current Local rows are queried independently and combined in memory;
    # there must be no write that copies them into historical_data.
    b=block("_sch_exp_fetch","_SCH_WEEKDAY_ORDER")
    assert "INSERT INTO historical_data" not in b
    assert "UPDATE historical_data" not in b
    assert "DELETE FROM historical_data" not in b

def test_all_class_schedule_formats_share_one_effective_context():
    b=block("report_export","# ==============================================================================")
    c=b[b.index("if report_type == 'class_schedule':"):b.index("# For room_schedule")]
    assert "_sch_export_context" in c
    assert "_sch_export_bytes" in c
    for fmt in ("'csv'", "'xlsx'", "'docx'", "'pdf'"):
        assert fmt in c

def test_class_schedule_format_generators_do_not_query_schedule_tables_directly():
    for fn,next_marker in [
        ("_sch_gen_csv","def _sch_gen_xlsx"),
        ("_sch_gen_xlsx","def _sch_gen_docx"),
        ("_sch_gen_docx","def _sch_gen_pdf"),
    ]:
        b=block(fn,next_marker)
        assert "schedule_sessions" not in b
        assert "local_arrangement" not in b

def test_class_schedule_calendar_generators_do_not_query_schedule_tables_directly():
    for fn,next_marker in [
        ("_sch_gen_xlsx_calendar","def _sch_gen_docx_calendar"),
        ("_sch_gen_docx_calendar","def _sch_gen_pdf_calendar"),
    ]:
        b=block(fn,next_marker)
        assert "schedule_sessions" not in b
        assert "local_arrangement" not in b

def test_room_schedule_all_formats_share_effective_room_context():
    b=block("report_export","# ==============================================================================")
    r=b[b.index("if report_type == 'room_schedule':"):b.index("# Curriculum List")]
    assert "_room_report_context" in r
    assert "_room_report_gen_csv" in r
    assert "_room_report_gen_xlsx" in r
    assert "_room_report_gen_docx" in r
    assert "_room_report_gen_pdf" in r
    assert "_room_report_gen_xlsx_calendar" in r
    assert "_room_report_gen_docx_calendar" in r
    assert "_room_report_gen_pdf_calendar" in r

def test_class_schedule_table_and_calendar_both_resolve_through_sch_exp_fetch():
    b=block("_sch_export_context","def _sch_gen_csv")
    assert "rows   = _sch_exp_fetch(" in b
    assert "_sch_exp_fetch(cur, ay_ids, sem_types, programs, year_levels, merge=False)" in b
