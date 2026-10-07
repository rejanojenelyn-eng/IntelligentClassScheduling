from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manual_faculty_local_mode_uses_effective_schedule():
    text = (ROOT / 'app.py').read_text(errors='ignore')
    start = text.index("@app.route('/api/manual/faculty_schedule')")
    end = text.index("@app.route('/api/get_curriculum')", start)
    block = text[start:end]
    assert "scheduler_mode" in block
    assert "las_x.official_sessionid = ss.sessionid" in block
    assert "la_x.status = 'Published'" in block
    assert "la_x.is_active = TRUE" in block
    assert "'Local' AS schedule_source" in block


def test_manual_editor_passes_mode_and_section_identity():
    js = (ROOT / 'static/js/ACAD HEAD/manualEditor.acad2.js').read_text(errors='ignore')
    html = (ROOT / 'templates/academic/manualScheduleEditor.html').read_text(errors='ignore')
    assert 'faculty_schedule' in js and 'scheduler_mode=${_sm()}' in js
    assert 'section_schedule' in js and "section_id=${encodeURIComponent(document.getElementById('sel_section')?.value || '')}" in js
    assert 'faculty_schedule' in html and 'scheduler_mode=${typeof SCHED_MODE' in html
    assert 'section_schedule' in html and 'section_id=${encodeURIComponent(_sectId)}&scheduler_mode=' in html
