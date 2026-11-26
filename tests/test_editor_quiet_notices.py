"""Manual Editor: finished actions show a corner toast (nothing to click), and deleting a
schedule asks once — the second "Final Confirmation" is gone."""
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
HTML = (ROOT / 'templates/academic/manualScheduleEditor.html').read_text(encoding='utf-8')
JS = (ROOT / 'static/js/ACAD HEAD/manualEditor.acad2.js').read_text(encoding='utf-8')
BASE = (ROOT / 'static/js/ACAD HEAD/base.acad.js').read_text(encoding='utf-8')


def test_corner_toast_helper_exists_and_is_used_for_done_notices():
    assert 'function showCornerToast(title, msg, type, duration)' in BASE
    assert "if (typeof showCornerToast === 'function') { showCornerToast(title, msg, 'success', duration)" in HTML
    assert "_notifyDone('Saved as Draft'" in HTML
    assert "_notifyDone(auto ? 'Faculty Changed — Auto-saved' : 'Schedule Published'" in JS
    assert "_notifyDone('Schedule Deleted'" in JS
    for title in ("'Saved as Draft'", "'Schedule Deleted'"):
        assert f'showValidationModal({title}' not in HTML + JS


def test_deleting_a_schedule_asks_only_once():
    for src in (HTML, JS):
        assert not re.search(r"delete this schedule\?[^']*'\s*,\s*'Final Confirmation'", src)
        assert not re.search(r"delete this schedule\?\nThis action cannot be undone\.', 'Final Confirmation'", src)
    assert "'Delete Schedule'" in HTML and "'Delete Schedule')" in JS


def test_restore_and_draft_publish_ask_only_once():
    dv = (ROOT / 'static/js/ACAD HEAD/draftView.acad.js').read_text(encoding='utf-8')
    assert 'Final Confirmation' not in dv and "'Publish Schedule'" in dv
    s = HTML.index("It replaces this section's current Draft.")
    assert 'Final Confirmation' not in HTML[s - 600:s + 900]


def test_editor_template_compiles():
    # A stray Jinja tag (even inside an HTML comment) breaks the whole page with a 500.
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(ROOT / 'templates')))
    env.parse((ROOT / 'templates/academic/manualScheduleEditor.html').read_text(encoding='utf-8'))
