from pathlib import Path

APP=Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

def block(a,b):
    s=APP.index(f"def {a}")
    e=APP.index(f"def {b}",s)
    return APP[s:e]

def test_teaching_assignment_load_uses_official_schedule_tables():
    b=block("api_faculty_teaching_assignments","api_load_draft")
    assert "FROM schedule_version sv" in b
    assert "JOIN schedule sc" in b
    assert "JOIN schedule_sessions ss" in b
    assert "sv.status IN ('Published','Draft')" in b

def test_teaching_assignment_load_does_not_merge_local_arrangements():
    b=block("api_faculty_teaching_assignments","api_load_draft")
    assert "local_arrangement_sessions" not in b
    assert "local_arrangement la" not in b

def test_faculty_caps_remain_official_assignment_caps():
    b=block("api_faculty_teaching_assignments","api_load_draft")
    assert "faculty_load.get_faculty_caps(fac)" in b
    assert "max_units = _reg + _pt + _teach_sub" in b

def test_load_hours_are_derived_from_official_session_duration():
    b=block("api_faculty_teaching_assignments","api_load_draft")
    assert "EXTRACT(EPOCH FROM (ts_e.timevalue - ts_s.timevalue))" in b
    assert "Actual scheduled duration of THIS time slice" in b

def test_merged_class_load_is_still_official_assignment_logic():
    b=block("api_faculty_teaching_assignments","api_load_draft")
    assert "_merge_groups" in b
    assert "_merge_in_scope" in b
    assert "merge_load_policy" in b
