"""Save as Local Arrangement: the prompt lists the slices being saved, and the reason
is optional (a blank one is replaced by an automatic summary for the audit trail)."""
from pathlib import Path
ROOT = Path(__file__).parents[1]
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")


def _fn(sig):
    s = HTML.index(sig)
    return HTML[s:HTML.index("\n}\n", s)]


def test_prompt_shows_the_changed_slices():
    assert 'id="localReasonSlices"' in HTML
    body = _fn("async function publishLocalArrangementFromEditor()")
    assert "_showLocalReasonModal(_changes)" in body


def test_reason_is_optional_with_automatic_fallback():
    body = _fn("async function publishLocalArrangementFromEditor()")
    assert "|| _localAutoReason(_changes)" in body
    assert "Reason Required" not in body
    vh = _fn("async function publishLocalArrangement(arrId, btn)")
    assert "|| 'Published from Version History'" in vh and "Reason Required" not in vh
