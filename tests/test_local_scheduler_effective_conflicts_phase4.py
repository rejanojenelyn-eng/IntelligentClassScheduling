"""Phase 4 regression tests — Local effective schedule/conflict validation."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(p): return ROOT.joinpath(p).read_text(encoding="utf-8-sig")
def block(src,a,b):
    s=src.index(f"def {a}"); e=src.index(f"def {b}",s); return src[s:e]

def test_conflict_endpoint_requires_section():
    b=block(read("app.py"),"api_local_check_room_conflicts","get_room_schedule")
    assert "A section is required for Local Scheduler conflict validation." in b

def test_effective_official_excludes_published_local_override():
    b=block(read("app.py"),"api_local_check_room_conflicts","get_room_schedule")
    assert "las_x.official_sessionid=ss.sessionid" in b
    assert "la_x.status='Published'" in b
    assert "la_x.is_active=TRUE" in b


def test_candidate_does_not_conflict_with_own_official_subject():
    b=block(read("app.py"),"api_local_check_room_conflicts","get_room_schedule")
    assert "ss.sessionid <> %s" in b
    assert "c['official_sessionid']" in b


def test_effective_validation_checks_official_and_local():
    b=block(read("app.py"),"api_local_check_room_conflicts","get_room_schedule")
    assert "'Official' AS source" in b
    assert "'Local' AS source" in b
    assert "la.status='Published'" in b
    assert "la.is_active=TRUE" in b


def test_room_faculty_section_dimensions_exist():
    b=block(read("app.py"),"api_local_check_room_conflicts","get_room_schedule")
    assert "_add(r,'Room')" in b
    assert "_add(r,'Faculty')" in b
    assert "_add(r,'Section')" in b


def test_candidate_vs_candidate_is_checked():
    b=block(read("app.py"),"api_local_check_room_conflicts","get_room_schedule")
    assert "Candidate-vs-candidate hard conflicts." in b
    assert "a['starttimeid'] < b['endtimeid']" in b
    assert "a['endtimeid'] > b['starttimeid']" in b


def test_frontend_sends_subject_faculty_and_section():
    h=read("templates/academic/manualScheduleEditor.html")
    assert "subject_code:" in h
    assert "faculty_id:" in h
    assert "section_id:" in h
    assert "/api/local/check_room_conflicts" in h

def test_frontend_validation_is_fail_closed():
    h=read("templates/academic/manualScheduleEditor.html")
    assert "Validation Unavailable" in h
    # Any non-success conflict-check outcome (network, server, rejected) stops the save.
    assert "if (cResult.kind !== 'ok') {" in h
    assert "Fail closed: never save an unvalidated Local arrangement." in h

def test_publish_revalidates_current_effective_schedule():
    b=block(read("app.py"),"api_publish_local_arrangement","api_restore_local_arrangement")
    assert "current effective schedule" in b
    assert "conflicts = []" in b
    assert "This Draft is no longer conflict-free." in b


def test_publish_conflict_check_happens_before_archive():
    b=block(read("app.py"),"api_publish_local_arrangement","api_restore_local_arrangement")
    assert b.index("if conflicts:") < b.index("SET status = 'Archived'")


def test_publish_conflict_ui_preserves_current_published_message():
    h=read("templates/academic/manualScheduleEditor.html")
    assert "_presentLocalPublishConflicts" in h
    assert "The currently Published Local Arrangement remains active." in h
