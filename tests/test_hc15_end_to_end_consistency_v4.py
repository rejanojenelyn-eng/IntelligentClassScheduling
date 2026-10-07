"""Static regression guards for V4 HC15 end-to-end consistency."""
from pathlib import Path

APP = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')


def _block(start, end):
    a = APP.index(start)
    b = APP.index(end, a)
    return APP[a:b]


def test_generation_cross_check_excludes_official_occurrences_replaced_by_published_local():
    b = _block('def _check_cross_schedule_conflicts', 'def _selected_row_problems')
    assert "la_x.status = 'Published'" in b
    assert 'las_x.official_sessionid = ss.sessionid' in b
    assert "la.status = 'Published'" in b
    assert 'published.extend(cur.fetchall() or [])' in b


def test_generation_cross_check_uses_shared_resource_and_merge_primitives():
    b = _block('def _check_cross_schedule_conflicts', 'def _selected_row_problems')
    assert '_ctx_conflicts.overlapping_resource_conflicts' in b
    assert '_hc_adapter.is_valid_merge' in b
    assert "'section_name': ex.get('sectionname')" in b


def test_publish_reuses_generation_hc15_validator():
    b = _block('def api_approve_schedule', "@app.route('/api/schedule")
    assert '_check_cross_schedule_conflicts(' in b
    assert "'with the current effective schedule.'" in b
