from pathlib import Path
ROOT=Path(__file__).parents[1]
APP=(ROOT/"app.py").read_text(encoding="utf-8")
GEN=(ROOT/"static/js/ACAD HEAD/scheduleGeneration.acad.js").read_text(encoding="utf-8")
HTML=(ROOT/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
JS=(ROOT/"static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")

def endpoint(name):
    s=APP.index(name)
    try: e=APP.index("@app.route",s+20)
    except ValueError: e=len(APP)
    return APP[s:e]

def test_generation_complete_partial_failure_handoff_contract():
    assert "unsaved_incomplete_subjects" in GEN
    assert "mode:          'unsaved_only'" in GEN
    assert "schedule_data: _unsavedRows" in GEN
    assert "mode:          'full_fallback'" in GEN
    assert "schedule_data: currentScheduleData" in GEN

def test_manual_consumes_fallback_once_and_dedupes():
    assert "sessionStorage.removeItem('_sched_gen_transfer')" in HTML
    assert "function _generatorPendingKey" in HTML
    assert "if (!existingKeys.has(key)" in HTML

def test_tba_subject_survives_partial_generation():
    assert "const days = n.days.length ? n.days : [''];" in HTML
    assert "isIncompleteFallback: transfer.mode === 'unsaved_only'" in HTML

def test_official_to_local_uses_published_official_as_base():
    b=endpoint("def api_manual_existing_sessions")
    assert "sv.status = 'Published'" in b
    assert "las.official_sessionid" in b
    assert "la.sectionid, sec_local.sectionname" in b

def test_local_mode_is_propagated_in_subject_and_sibling_loads():
    assert "scheduler_mode=${_sm()}" in JS
    assert "scheduler_mode=${typeof SCHED_MODE" in HTML

def test_multiple_occurrences_are_hydrated():
    assert "for (const sess of uniqueSessions)" in HTML

def test_day_and_time_recommendations_are_top_three_and_clickable():
    assert ".filter(r => r && feasibleDays.has(r.day))" in HTML
    assert "onmousedown=\"_chooseRecommendedDay" in HTML
    assert "onmousedown=\"_chooseRecommendedTime" in HTML
    assert "${r.start} – ${r.end}" in HTML

def test_recommendations_do_not_auto_mutate_on_dss_response():
    s=JS.index("window._dssRecommendedDays")
    e=JS.index("const isPreferredType",s)
    b=JS[s:e]
    assert "_scheduleRecommendationRefresh()" in b
    assert "tsSel(" not in b
    assert ".value =" not in b

def test_coalesced_refresh_has_no_feedback_loop():
    s=HTML.index("window._scheduleRecommendationRefresh")
    e=HTML.index("};",s)+2
    b=HTML[s:e]
    assert "if (_recommendationRefreshFrame) return;" in b
    assert b.count("document.querySelectorAll('.ts-row')")==1
    assert "fetch(" not in b and "dispatchEvent" not in b and "tsSel(" not in b

def test_manual_time_controls_remain_available():
    assert 'ts-rec-gen">OTHERS' in HTML
    assert 'id="tsst-txt-${id}"' in HTML
    assert 'id="tset-txt-${id}"' in HTML

