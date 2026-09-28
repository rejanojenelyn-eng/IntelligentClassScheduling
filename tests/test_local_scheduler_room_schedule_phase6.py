from pathlib import Path
APP=Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
def block(a,b):
    s=APP.index(f"def {a}")
    e=APP.index(f"def {b}",s)
    return APP[s:e]

def test_room_schedule_effective_official_uses_exact_occurrence():
    b=block("get_room_schedule","api_rooms_occupancy_by_day")
    assert "las.official_sessionid = ss.sessionid" in b
    assert "la.status = 'Published'" in b
    assert "la.is_active = TRUE" in b
    assert "la.sectionid = s.sectionid" in b

def test_room_schedule_no_subject_level_displacement():
    b=block("get_room_schedule","api_rooms_occupancy_by_day")
    assert "UPPER(las.subjectcode) = UPPER(cs.subjectcode)" not in b
    assert "local_displaced_subjects" not in b

def test_room_schedule_returns_local_only_when_published_active():
    b=block("get_room_schedule","api_rooms_occupancy_by_day")
    assert '"la.is_active = TRUE"' in b and '"la.status = \'Published\'"' in b

def test_room_schedule_exposes_source_identity():
    b=block("get_room_schedule","api_rooms_occupancy_by_day")
    assert "'Official' AS schedule_source" in b
    assert "'Local' AS schedule_source" in b
    assert "ss.sessionid AS official_sessionid" in b
    assert "las.official_sessionid" in b

def test_available_rooms_uses_effective_official_occupancy():
    b=block("api_faculty_available_rooms","_structured_violation")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "la_x.status = 'Published'" in b
    assert "la_x.is_active = TRUE" in b
    assert "la_x.sectionid = s.sectionid" in b
    assert "la_x.semesterid = s.semesterid" in b

def test_available_rooms_adds_active_published_local_occupancy():
    b=block("api_faculty_available_rooms","_structured_violation")
    assert "Published local arrangement occupancy" in b
    assert "la.status = 'Published'" in b
    assert "la.is_active = TRUE" in b

def test_available_rooms_does_not_use_subject_level_displacement():
    b=block("api_faculty_available_rooms","_structured_violation")
    assert "UPPER(las_x.subjectcode)" not in b

def test_bulk_room_occupancy_uses_exact_local_displacement():
    b=block("api_rooms_occupancy_by_day","api_faculty_schedule")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "la_x.status = 'Published'" in b
    assert "la_x.is_active = TRUE" in b
    assert "la_x.sectionid = s.sectionid" in b

def test_bulk_room_occupancy_keeps_official_drafts_for_editor():
    b=block("api_rooms_occupancy_by_day","api_faculty_schedule")
    assert "sv.status IN ('Published', 'Draft')" in b
    assert "sv.status = 'Draft'" in b

def test_bulk_room_occupancy_adds_only_active_published_local():
    b=block("api_rooms_occupancy_by_day","api_faculty_schedule")
    assert "la.status = 'Published'" in b
    assert "la.is_active = TRUE" in b
    assert "local_rows" in b

def test_bulk_room_occupancy_filters_local_by_requested_period():
    b=block("api_rooms_occupancy_by_day","api_faculty_schedule")
    assert "ay_l.academicyearid = %s" in b
    assert "sem_l.semestertype = %s" in b
