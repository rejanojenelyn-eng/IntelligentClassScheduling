from pathlib import Path
ROOT=Path(__file__).parents[1]
APP=(ROOT/"app.py").read_text(encoding="utf-8")
HTML=(ROOT/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
JS=(ROOT/"static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")
CSS=(ROOT/"static/css/manualSchedule.css").read_text(encoding="utf-8")

def dss():
    s=APP.index("def api_dss_suggest")
    return APP[s:APP.index("@app.route",s+20)]

def test_backend_ranks_complete_pairs_not_independent_boundaries():
    b=dss()
    assert 'TRIM("Time") AS timedesc' in b
    assert 'key = (start_t, end_t)' in b
    assert '"times":   {"recommended": recommended_times}' in b

def test_context_is_primary_time_ranking_signal():
    b=dss()
    assert "time_pair_ctx" in b
    assert "x.get('context_count', 0), x.get('count', 0)" in b

def test_frontend_caches_without_auto_select():
    assert "window._dssRecommendedTimes" in JS
    block=JS[JS.index("window._dssRecommendedTimes")-100:JS.index("const isPreferredType")]
    assert "_scheduleRecommendationRefresh()" in block
    assert "tsSel(" not in block

def test_duration_is_pair_duration_and_filtered_per_slice():
    assert "function _sliceRequiredMinutes" in HTML
    assert "(em - sm) !== requiredMinutes" in HTML

def test_only_valid_editor_boundaries_are_recommended():
    assert "_TS_TIMES.includes(r.start) && _TS_TIMES.includes(r.end)" in HTML

def test_top_three_complete_pairs():
    assert ").slice(0, 3);" in HTML
    assert "ts-time-rec-title" in HTML
    assert "${r.start} – ${r.end}" in HTML

def test_click_fills_start_and_end_through_existing_pipeline():
    assert "tsSel('tsst', rowId, start);" in HTML
    assert "tsSel('tset', rowId, end);" in HTML

def test_manual_controls_are_retained():
    assert "OTHER / MANUAL TIME" in HTML
    assert 'id="tsst-txt-${id}"' in HTML
    assert 'id="tset-txt-${id}"' in HTML

def test_time_refresh_is_render_only():
    s=HTML.index("window._refreshTimeRecommendations")
    e=HTML.index("};",s)+2
    block=HTML[s:e]
    assert "fetch(" not in block
    assert "tsSel(" not in block

def test_compact_css_exists():
    assert ".ts-time-rec-box" in CSS
    assert ".ts-time-rec-item" in CSS
