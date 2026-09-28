"""Phase 5 regression tests — occurrence-level Local displacement lifecycle."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.joinpath("app.py").read_text(encoding="utf-8-sig")

def block(a, b):
    s = APP.index(f"def {a}")
    e = APP.index(f"def {b}", s)
    return APP[s:e]

def test_schema_has_exact_official_occurrence_link():
    assert "official_sessionid" in APP
    assert "REFERENCES public.schedule_sessions(sessionid)" in APP
    assert "idx_local_session_official_source" in APP

def test_save_persists_official_sessionid():
    b = block("api_save_local_arrangement", "api_get_local_arrangements")
    assert "official_sessionid" in b
    assert "sess.get('official_sessionid')" in b

def test_save_time_effective_official_displacement_is_occurrence_level():
    b = block("api_local_check_room_conflicts", "get_room_schedule")
    assert "las_x.official_sessionid=ss.sessionid" in b
    assert "ss.sessionid <> %s" in b
    # Guard against the old broad subject-only suppression pattern.
    assert "UPPER(las_x.subjectcode) = UPPER(cs.subjectcode)" not in b

def test_save_time_keeps_other_meetings_of_same_subject_effective():
    b = block("api_local_check_room_conflicts", "get_room_schedule")
    # Suppression identity must be schedule_sessions.sessionid, not subjectcode.
    assert "las_x.official_sessionid=ss.sessionid" in b
    assert "official_sessionid" in b

def test_publish_revalidation_uses_occurrence_identity():
    b = block("api_publish_local_arrangement", "api_restore_local_arrangement")
    assert "las_x.official_sessionid = ss.sessionid" in b
    assert "ss.sessionid <> %s" in b
    assert "ds.get('official_sessionid')" in b

def test_publish_checks_conflicts_before_archiving_previous_local():
    b = block("api_publish_local_arrangement", "api_restore_local_arrangement")
    assert b.index("if conflicts:") < b.index("SET status = 'Archived'")

def test_archive_deactivates_previous_published_local():
    b = block("api_publish_local_arrangement", "api_restore_local_arrangement")
    archive = b[b.index("SET status = 'Archived'"):b.index("SET status = 'Published'")]
    assert "is_active = FALSE" in archive

def test_new_published_local_is_explicitly_active():
    b = block("api_publish_local_arrangement", "api_restore_local_arrangement")
    publish = b[b.index("SET status = 'Published'"):]
    assert "is_active = TRUE" in publish

def test_restore_creates_inactive_draft():
    b = block("api_restore_local_arrangement", "api_local_check_room_conflicts")
    assert "FALSE, 'Draft'" in b

def test_restore_preserves_official_sessionid():
    b = block("api_restore_local_arrangement", "api_local_check_room_conflicts")
    assert "official_sessionid" in b
    assert "s.get('official_sessionid')" in b

def test_effective_queries_only_use_active_published_local():
    b = block("api_local_check_room_conflicts", "get_room_schedule")
    assert "la_x.status='Published'" in b
    assert "la_x.is_active=TRUE" in b
    assert "la.status='Published'" in b
    assert "la.is_active=TRUE" in b
