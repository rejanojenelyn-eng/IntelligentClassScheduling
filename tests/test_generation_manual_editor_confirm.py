"""Go to Manual Editor with a generated/retrieved schedule that is NOT eligible for
publishing asks first (it would open in the editor with the same issues)."""
from pathlib import Path
JS = (Path(__file__).parents[1] / "static/js/ACAD HEAD/scheduleGeneration.acad.js").read_text(encoding="utf-8")


def test_manual_editor_confirms_when_not_eligible():
    s = JS.index("btnManualEditor.addEventListener('click'")
    body = JS[s:s + 3000]
    chk = body.index("const _elig = _approvalState();")
    assert "if (!_elig.eligible) {" in body
    assert "'Schedule Not Eligible for Publishing'" in body
    assert "if (!_go) return;" in body
    assert chk < body.index("fetch('/api/schedule/check-existing'")


def test_confirm_labels_go_through_showinfo_options():
    # showInfo() overwrites the button text, so labels must be passed as options.
    assert "_confirmBtn.textContent = '" not in JS
    assert "{ confirmLabel: 'Continue to Manual Editor' }" in JS
    assert "{ confirmLabel: 'Override' }" in JS
