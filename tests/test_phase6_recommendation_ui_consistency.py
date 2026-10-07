from pathlib import Path
ROOT=Path(__file__).parents[1]
HTML=(ROOT/"templates/academic/manualScheduleEditor.html").read_text(encoding="utf-8")
CSS=(ROOT/"static/css/manualSchedule.css").read_text(encoding="utf-8")
JS=(ROOT/"static/js/ACAD HEAD/manualEditor.acad2.js").read_text(encoding="utf-8")

def test_day_and_time_share_one_recommendation_ui_language():
    # Day and Time recommendations both live inside their dropdowns, room-style.
    assert 'ts-ss-sect-hdr ts-ss-sect-opt ts-day-rec-title">RECOMMENDATIONS' in HTML
    assert 'ts-ss-sect-hdr ts-ss-sect-opt ts-rec-gen">RECOMMENDATIONS' in HTML

def test_both_sections_use_same_heading_wording():
    assert 'ts-day-rec-title">RECOMMENDATIONS' in HTML  # dropdown section header
    assert 'ts-rec-gen">RECOMMENDATIONS' in HTML

def test_time_pair_remains_visible_as_complete_range():
    assert "${r.start} – ${r.end}</div>" in HTML

def test_manual_start_end_controls_are_preserved():
    assert 'ts-rec-gen">OTHERS' in HTML
    assert 'id="tsst-txt-${id}"' in HTML
    assert 'id="tset-txt-${id}"' in HTML

def test_ui_has_narrow_panel_overflow_protection():
    assert "@media (max-width: 1180px)" in CSS
    assert "grid-template-columns: minmax(0, 1fr);" in CSS
    assert "overflow-wrap: anywhere" in CSS

def test_ui_phase_does_not_add_data_fetches():
    # Phase 6 is CSS/markup only: existing DSS call count remains one in this JS.
    assert JS.count("/api/dss/suggest") == 1

def test_no_absolute_positioning_for_recommendation_sections():
    phase6=CSS[CSS.index("Phase 6 — Manual Editor recommendation UI consistency"):]
    assert "position: absolute" not in phase6

def test_flat_rows_not_large_cards():
    phase6=CSS[CSS.index("Phase 6 — Manual Editor recommendation UI consistency"):]
    assert "border-radius: 0" in phase6
    assert "grid-template-columns: 28px minmax(0, 1fr)" in phase6
