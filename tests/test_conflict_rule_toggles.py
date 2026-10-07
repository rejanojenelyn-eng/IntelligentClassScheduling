"""Admin Settings on/off switches for HC6, HC14 and Core Conflict Validation
(HC10 / HC11 / HC12 / HC15, one switch per rule).

The conflict switches gate OFFICIAL scheduling only (generation, Manual Editor,
Save Draft, Publish incl. cross-schedule). The Local Scheduler and Make-up /
Schedule Adjustment requests + approvals never read them and stay protected.
"""
import re
from datetime import time
from pathlib import Path

import app as app_module
from scheduler import CSPValidator

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = (ROOT / 'app.py').read_text(encoding='utf-8')
SETTINGS_HTML = (ROOT / 'templates/admin/settings_admin.html').read_text(encoding='utf-8')
SETTINGS_JS = (ROOT / 'static/js/ADMIN/settings.admin.js').read_text(encoding='utf-8')
EDITOR_HTML = (ROOT / 'templates/academic/manualScheduleEditor.html').read_text(encoding='utf-8')

CONFLICT_KEYS = ('hc_faculty_conflict_enabled', 'hc_room_conflict_enabled',
                 'hc_section_conflict_enabled', 'hc_cross_schedule_enabled')


def _cls(subject, faculty, room, section):
    return {'subject_code': subject, 'faculty_id': faculty, 'room_id': room,
            'section_id': section, 'section_name': f'S{section}', 'programcode': 'BSIT',
            'day': 'Monday', 'days_list': ['Monday'],
            'start_time': time(9, 0), 'end_time': time(10, 30)}


def _rules(cfg, schedule):
    return {v.get('rule') for v in CSPValidator(config=cfg).validate(schedule, {})}


def test_validator_obeys_each_conflict_switch():
    both = [_cls('IT101', 'F1', 1, 10), _cls('IT102', 'F1', 1, 10)]   # same faculty, room, section
    on = _rules({}, both)
    assert {'HC10', 'HC11', 'HC12'} <= on
    for key, rule in (('hc_faculty_conflict_enabled', 'HC10'), ('hc_room_conflict_enabled', 'HC11'),
                      ('hc_section_conflict_enabled', 'HC12')):
        off = _rules({key: 0}, both)
        assert rule not in off, key
        assert ({'HC10', 'HC11', 'HC12'} - {rule}) <= off, key


def test_publish_carried_forward_overlaps_obey_switches():
    sub, car = [_cls('IT101', 'F1', 1, 10)], [_cls('IT102', 'F1', 1, 10)]
    fn = app_module._publish_carried_forward_conflicts
    assert {v['rule'] for v in fn(sub, car, config={})} >= {'HC10', 'HC11', 'HC12'}
    assert fn(sub, car, config={k: 0 for k in CONFLICT_KEYS[:3]}) == []


def test_cross_schedule_check_reads_hc15_and_per_rule_switches():
    fn = APP_SRC[APP_SRC.index('def _check_cross_schedule_conflicts'):]
    fn = fn[:fn.index('\ndef ', 10)]
    assert "hc_cross_schedule_enabled" in fn and 'return [], 0' in fn
    assert "if not _dim_enabled.get(dim, True):" in fn


def test_local_and_request_paths_never_read_conflict_switches():
    for marker in ("def api_save_local_arrangement", "def api_publish_local_arrangement",
                   "def api_local_check_room_conflicts", "def _request_conflict_summary",
                   "def api_requests_decide"):
        start = APP_SRC.index(marker)
        body = APP_SRC[start:APP_SRC.index('\n@app.route(', start)]
        for key in CONFLICT_KEYS:
            assert key not in body, (marker, key)


def test_settings_cards_have_switches_and_are_loaded():
    for tog in ('tog-time-blocks', 'tog-capacity', 'tog-faculty-conflict', 'tog-room-conflict',
                'tog-section-conflict', 'tog-cross-schedule'):
        assert f'id="{tog}"' in SETTINGS_HTML, tog
        assert f"'{tog}'" in SETTINGS_JS, tog
    assert 'not independently disabled from this screen' not in SETTINGS_HTML
    assert "saveConstraint('time_blocks'" in SETTINGS_HTML and "saveConstraint('capacity'" in SETTINGS_HTML


def test_manual_editor_gates_official_only():
    helper = EDITOR_HTML[EDITOR_HTML.index('async function _officialConflictRules'):]
    helper = helper[:helper.index('\n}\n')]
    assert "SCHED_MODE === 'local') return allOn" in helper
    assert "catch (_) { return allOn; }" in helper
    for guard in ('if (_ruleOn.room && _ruleOn.cross && roomVal', 'if (_ruleOn.room && !_mergeConfirmed)',
                  'if (_ruleOn.faculty && _ruleOn.cross && facVal', '_ruleOn.faculty ? pendingManualSchedule',
                  'if (_ruleOn.section) try {', '_ruleOn.section ? pendingManualSchedule'):
        assert guard in EDITOR_HTML, guard
    assert all(k in APP_SRC[APP_SRC.index("def api_hc_config"):APP_SRC.index("def api_manual_subject_info")]
               for k in CONFLICT_KEYS)
