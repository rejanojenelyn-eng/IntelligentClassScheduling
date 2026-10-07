from pathlib import Path
ROOT = Path(__file__).parents[1]
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")


def _fn(sig):
    s = HTML.index(sig)
    return HTML[s:HTML.index("\n}\n", s)]


def test_auto_select_waits_for_the_url_section_at_startup():
    assert "if (initSect && initSectName) window._autoSelectHold = true;" in HTML
    assert "await selectBcSect(initSect, initSectName);" in HTML
    assert ".finally(() => _releaseAutoSelectHold());" in HTML
    assert "if (window._autoSelectHold) return;" in _fn("async function _localAutoSelectFirstSubject()")


def test_local_auto_select_retries_a_blocked_published_pick_and_selects_slice_one():
    body = _fn("async function _localAutoSelectFirstSubject()")
    assert "_blocked()" in body and "await _clickPick();" in body
    assert "await _autoSelectFirstSlice(pick.value);" in body
    slice_fn = _fn("async function _autoSelectFirstSlice(subjectCode)")
    assert "_selectSlice(id, null)" in slice_fn
    assert "if (_activeSliceId != null) return;" in slice_fn     # never overrides the user's pick
