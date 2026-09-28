from pathlib import Path
ROOT=Path(__file__).parents[1]
APP=(ROOT/"app.py").read_text(encoding="utf-8")
HTML=(ROOT/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
JS2=(ROOT/"static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")

def endpoint_block():
    s=APP.index("def api_manual_existing_sessions")
    e=APP.index("@app.route",s+20)
    return APP[s:e]

def test_official_payload_carries_all_hydration_dimensions():
    b=endpoint_block()
    for token in ["ss.sessionid", "ss.starttimeid", "ss.endtimeid", "ss.daydesc",
                  "r.roomid", "r.roomname", "f.employeenumber",
                  "sec.sectionid", "sec.sectionname", "start_fmt", "end_fmt"]:
        assert token in b

def test_local_payload_carries_exact_official_occurrence_and_section():
    b=endpoint_block()
    assert "las.official_sessionid" in b
    assert "la.sectionid, sec_local.sectionname" in b
    assert "las.faculty_employeenumber" in b
    assert "las.roomid" in b
    assert "las.starttimeid" in b and "las.endtimeid" in b

def test_local_hydration_prefers_active_local_then_official_published():
    b=endpoint_block()
    assert "la2.is_active  = TRUE" in b
    assert "la2.status    IN ('Published', 'Draft')" in b
    assert "CASE WHEN la2.status = 'Published' THEN 0 ELSE 1 END" in b
    assert "rows = [dict(r) for r in pub_rows]" in b

def test_section_is_strictly_scoped_in_local_lookup():
    b=endpoint_block()
    assert "la2.sectionid  = %s" in b
    assert "base_filters.append(\"sec.sectionid = %s\")" in b

def test_frontend_preserves_backend_section_identity():
    assert "String(sess.sectionid || document.getElementById('sel_section')" in HTML
    assert "sess.sectionname ||" in HTML

def test_frontend_preserves_all_slices_not_just_first():
    assert "for (const sess of uniqueSessions)" in HTML
    assert "official_sessionid: sess.official_sessionid || sess.sessionid || null" in HTML

def test_subject_load_requests_current_scheduler_mode():
    assert "scheduler_mode=${_sm()}" in JS2

def test_sibling_lookup_is_scheduler_mode_correct():
    marker="const _sibUrl = `/api/manual/existing_sessions"
    s=JS2.index(marker)
    e=JS2.index(";",s)
    assert "scheduler_mode=${_sm()}" in JS2[s:e]
