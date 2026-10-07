"""Leaving the Manual Editor with unsaved changes shows the app's styled Unsaved Changes
popup (the browser's 'Leave site?' dialog is only the fallback for tab close / reload)."""
import re
from pathlib import Path
ROOT = Path(__file__).parents[1]
HTML = (ROOT / "templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")


def test_guard_and_link_interceptor_exist():
    assert "async function _guardedNavigate(url)" in JS
    assert "'Unsaved Changes'" in JS[JS.index("async function _guardedNavigate"):]
    assert "closest('a[href]')" in JS


def test_editor_never_navigates_around_the_guard():
    # Every in-app navigation in the editor template goes through _guardedNavigate.
    assert not re.search(r"window\.location\.href\s*=", HTML)
    assert HTML.count("_guardedNavigate(") >= 4
