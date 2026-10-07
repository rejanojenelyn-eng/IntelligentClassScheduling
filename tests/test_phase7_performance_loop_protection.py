from pathlib import Path
ROOT=Path(__file__).parents[1]
HTML=(ROOT/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
JS=(ROOT/"static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")
def test_dss_coalesces_refresh():
    b=JS[JS.index("window._dssRecommendedDays"):JS.index("const isPreferredType")]
    assert "_scheduleRecommendationRefresh()" in b
    assert "_refreshDayRecommendations()" not in b and "_refreshTimeRecommendations()" not in b
def test_one_frame_guard():
    assert "if (_recommendationRefreshFrame) return;" in HTML
    assert "requestAnimationFrame(run)" in HTML
def test_combined_refresh_walks_once():
    s=HTML.index("window._scheduleRecommendationRefresh"); e=HTML.index("};",s)+2
    b=HTML[s:e]
    assert b.count("document.querySelectorAll('.ts-row')")==1
    assert "_renderDayRecommendations(id);" in b and "_renderTimeRecommendations(id);" in b
def test_refresh_has_no_fetch_or_selection():
    s=HTML.index("window._scheduleRecommendationRefresh"); e=HTML.index("};",s)+2
    b=HTML[s:e]
    assert "fetch(" not in b and "tsSel(" not in b and "dispatchEvent" not in b
def test_identical_markup_not_replaced():
    assert "if (list.dataset.recSig === sig) return;" in HTML                   # time recs
    assert "if (list.innerHTML !== nextHtml) list.innerHTML = nextHtml;" in HTML  # day dropdown
def test_no_recommendation_mutation_observer():
    s=HTML.index("function _renderDayRecommendations"); e=HTML.index("function addNewTimeSlot")
    assert "MutationObserver" not in HTML[s:e]
def test_single_dss_endpoint_reference():
    assert JS.count("/api/dss/suggest")==1

