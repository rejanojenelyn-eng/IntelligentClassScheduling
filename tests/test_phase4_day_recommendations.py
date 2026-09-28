from pathlib import Path
ROOT=Path(__file__).parents[1]
APP=(ROOT/"app.py").read_text(encoding="utf-8")
HTML=(ROOT/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
JS=(ROOT/"static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")
CSS=(ROOT/"static/css/manualSchedule.css").read_text(encoding="utf-8")

def dss_block():
    s=APP.index("def api_dss_suggest")
    e=APP.index("@app.route",s+20)
    return APP[s:e]

def test_day_history_is_subject_scoped():
    b=dss_block()
    assert 'TRIM("Day/s") AS daydesc' in b
    assert 'REGEXP_REPLACE(UPPER(TRIM("Subject Code"))' in b
    assert '"days":    {"recommended": recommended_days}' in b

def test_day_history_supports_multi_day_imports():
    b=dss_block()
    assert "re.split(r'\\s*[/,]\\s*', raw)" in b
    assert "'MON':'Monday'" in b and "'THU':'Thursday'" in b

def test_context_frequency_is_primary_ranking_signal():
    b=dss_block()
    assert "day_ctx_counts" in b
    assert "x.get('context_count', 0), x.get('count', 0)" in b

def test_frontend_caches_day_recs_without_auto_selecting():
    assert "window._dssRecommendedDays" in JS
    block=JS[JS.index("window._dssRecommendedDays")-200:JS.index("const isPreferredType")]
    assert "_scheduleRecommendationRefresh()" in block
    assert ".value =" not in block

def test_recommendations_only_use_current_feasible_select_options():
    assert "const feasibleDays = new Set(Array.from(sel.options)" in HTML
    assert ".filter(r => r && feasibleDays.has(r.day))" in HTML

def test_only_top_three_are_shown():
    assert ".slice(0, 3)" in HTML

def test_click_uses_existing_day_change_pipeline():
    assert "function _chooseRecommendedDay" in HTML
    assert "onTsDayChange(rowId);" in HTML

def test_day_ui_is_compact_and_below_day_select():
    assert 'id="ts-day-rec-${id}"' in HTML
    assert ".ts-day-rec-box" in CSS
