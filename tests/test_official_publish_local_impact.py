"""Publishing an Official schedule archives the section's Local Arrangements; the
editor must confirm first (same / different / other-subject) and report it after."""
from pathlib import Path
ROOT = Path(__file__).parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")


def test_impact_endpoint_lists_local_and_original_official_slots():
    s = APP.index("def api_local_republish_impact")
    body = APP[s:APP.index("@app.route", s)]
    assert "la.status IN ('Draft', 'Published')" in body          # same scope as the archive step
    assert "oss.sessionid = las.official_sessionid" in body       # the Official slot it moved


def test_approve_confirms_before_publishing():
    s = JS.index("window._runManualApproveImpl = async function")
    body = JS[s:]
    chk = body.index("_confirmLocalRepublishImpact(selectedDrafts")
    assert chk < body.index("fetch('/api/schedule/approve'")


def test_confirmation_shows_each_class_going_back_to_official():
    s = JS.index("async function _confirmLocalRepublishImpact")
    body = JS[s:JS.index("window._runManualApproveImpl", s)]
    # each Local row: same-as-publish, or the Official slot it returns to
    # (the slot being published now, else its current Official slot)
    assert "sameAsPublish" in body and "backTo" in body
    assert "s.official_day ?" in body
    for text in ("LOCAL ARRANGEMENTS WILL BE REMOVED", "What will happen?",
                 "Local arrangements to be removed", "Continue publishing?",
                 "Back to Official:", "Yes, Publish"):
        assert text in body


def test_success_message_reports_archived_local_arrangements():
    assert "data.archived_local_arrangements" in JS


def test_replace_prompt_is_one_short_final_confirmation():
    assert "'Publish Schedule?'" in JS
    assert "already exists for this program and year level" not in JS
    # skipped when the Local Arrangements prompt already confirmed this publish
    assert "if (auto || _publishConfirmed) {" in JS
    assert "return ok ? 'confirmed' : false;" in JS
