"""
Fix #4 — a successful Re-generate Selected finishes the operation for the rows
it resolved: they are unchecked and their temporary locks dropped; failed rows
keep both for a retry; warnings come from the new result's own evaluation.

Fix #5 — "Generate New Schedule" in the No Previous Schedule modal runs the
normal Generate workflow immediately (one retrieval, one fresh generation, the
CURRENT filters), without a second click and without a retrieval loop.

The UI parts run the real scheduleGeneration.acad.js under Node
(tests/js/regen_cleanup_harness.js); the backend part uses the scripted solver
of test_reference_validation_and_progressive_regen.py.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

import app as app_module
from conftest import requires_db
from test_reference_validation_and_progressive_regen import _ScriptedSolver, _regen, _sel, _fixed

NO_ICONS = {'faculty': None, 'schedule': None, 'room': None, 'icons': 0}
ALL_OPEN = {'faculty': 'is-unlocked', 'schedule': 'is-unlocked', 'room': 'is-unlocked', 'icons': 3}


@pytest.fixture(scope='module')
def ui():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    harness = pathlib.Path(__file__).resolve().parent / 'js' / 'regen_cleanup_harness.js'
    out = subprocess.run([node, str(harness)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── Fix #4 ─────────────────────────────────────────────────────────────────

def test_c1_successful_row_is_unchecked_and_its_locks_removed(ui):
    assert ui['c1Selected']['checked'] and ui['c1Selected']['locks']['icons'] == 3
    assert ui['c1Payload']['A 101']['selected'] is True
    assert ui['c1']['checked'] is False and ui['c1']['locks'] == NO_ICONS
    assert ui['c1']['time'] is True                      # regenerated value kept


def test_c2_preserved_instructor_keeps_value_but_not_the_lock(ui):
    assert ui['c2LockedBefore']['faculty'] == 'is-locked'
    assert ui['c2Payload']['A 101']['lock']['faculty'] is True
    assert ui['c2'] == {'checked': False, 'locks': NO_ICONS, 'instructor': True}


def test_c2b_instructor_changed_by_regeneration_leaves_no_stale_state(ui):
    assert ui['keyChange'] == {'checked': False, 'locks': NO_ICONS, 'instructor': True}


def test_c3_every_successful_row_is_reset(ui):
    assert ui['c3'] == [{'checked': False, 'icons': 0}] * 3


def test_c4_partial_success_resets_only_the_successful_rows(ui):
    assert all(v['selected'] for k, v in ui['c4Payload'].items() if k != 'D 104')
    assert ui['c4']['A'] == {'checked': False, 'icons': 0}
    assert ui['c4']['C'] == {'checked': False, 'icons': 0, 'room': True}
    assert ui['c4']['B'] == {'checked': True, 'locks': ui['c4BLocksBefore']}   # ready for retry
    assert ui['c4']['modalTitle'] == 'Some Rows Could Not Be Regenerated'


def test_c5_total_failure_preserves_selection_and_locks(ui):
    assert ui['c5']['A'] == {'checked': True, 'locks': ui['c5']['before']['A']}
    assert ui['c5']['B'] == {'checked': True, 'locks': ui['c5']['before']['B']}
    assert ui['c5']['modalTitle'] == 'Regeneration Failed'


def test_c6_resolved_conflict_disappears_through_the_new_evaluation(ui):
    assert ui['before']['aFlags'] == 1
    assert ui['c1']['flags'] == 0 and ui['c1']['panelHidden'] is True


def test_c7_new_legitimate_warning_is_shown(ui):
    assert ui['c7']['cFlags'] == 1
    assert ui['c7']['panelHidden'] is False and ui['c7']['panelText'] == '1 Schedule Conflict'


def test_c8_select_all_checkbox_follows_the_new_selection(ui):
    assert ui['c8AfterC1'] is False and ui['c8AfterC3'] is False and ui['c8AfterC4'] is False


def test_c9_reselecting_a_successful_row_starts_all_unlocked(ui):
    assert ui['c9'] == ALL_OPEN


def test_c10_unselected_rows_stay_unchanged(ui):
    assert all(o == {'checked': False, 'icons': 0} for o in ui['c1']['others'])
    assert ui['c10D'] == {'checked': False, 'icons': 0}
    for payload in (ui['c1Payload'], ui['c2Payload'], ui['c4Payload']):
        assert payload['D 104'] == {'selected': False, 'lock': {'faculty': True, 'room': True, 'schedule': True}}


@requires_db
def test_c4_backend_partial_success_applies_resolved_rows_and_restores_failed_ones(monkeypatch):
    # A resolves (new room); B cannot (Time/Days user-locked); C is unselected.
    solver = _ScriptedSolver(b_needs=('room', 'schedule'))
    rows = [_sel('A', (True, True, False)),
            _sel('B', (True, True, False), user=(False, True, False), room_id=None, room='TBA'),
            _fixed('C')]
    body = _regen(monkeypatch, solver, rows)
    assert body['result_status'] == 'REGENERATION_PARTIAL' and body['success'] is True
    assert body['failed_subjects'] == ['B']
    assert body['error'] == 'B could not be resolved while Time/Days is locked by you.'
    by = {g['subject_code']: g for g in body['schedule_data']}
    assert by['A']['room_id'] in (19, 20, 21, 22, 23)            # regenerated value applied
    assert by['B']['room'] == 'TBA' and not by['B'].get('room_id')   # sent back unchanged
    assert by['C']['room_id'] == 18
    assert body['evaluation'] is not None                       # evaluated as a whole


@requires_db
def test_c5_backend_total_failure_still_applies_nothing(monkeypatch):
    solver = _ScriptedSolver(b_needs=('never',))
    body = _regen(monkeypatch, solver, [_sel('B', (True, True, True), room_id=None, room='TBA')])
    assert body['result_status'] == 'REGENERATION_INFEASIBLE' and 'schedule_data' not in body


# ── Fix #5 ─────────────────────────────────────────────────────────────────

def test_d1_existing_previous_schedule_is_retrieved_normally(ui):
    assert ui['d1'] == {'urls': ['/api/schedule/retrieve-previous'], 'shown': True, 'retrieveChecked': True}


def test_d2_no_previous_schedule_shows_the_modal(ui):
    assert ui['d2ModalTitle'] == 'No Previous Schedule Available'


def test_d3_stay_on_page_generates_nothing_and_keeps_everything(ui):
    d3 = ui['d3']
    assert d3['urls'] == ['/api/schedule/retrieve-previous']
    assert d3['stillOld'] is True and d3['retrieveChecked'] is True and d3['program'] == 'BPAFA'
    assert d3['btnDisabled'] is False


def test_d4_d5_generate_new_starts_fresh_generation_without_a_second_click(ui):
    assert ui['d5InFlight']['urls'] == ['/api/schedule/retrieve-previous', '/api/schedule/generate']
    assert ui['d5InFlight']['loading'] is True and ui['d5InFlight']['btnDisabled'] is True


def test_d6_d7_d8_one_retrieval_then_exactly_one_fresh_generation(ui):
    assert ui['d4']['urls'] == ['/api/schedule/retrieve-previous', '/api/schedule/generate']
    assert ui['d4']['retrieveChecked'] is False


def test_d9_current_filters_are_used_not_the_old_display(ui):
    body = ui['d4']['genBody']
    assert (body['program'], body['yearLevel'], body['section'], body['curriculum'],
            body['acadYear'], body['term']) == ('BPAFA', 4, '777', '2022-2023', 'AY2627', 'A')
    assert 'locked_sessions' not in body
    assert 'BPAFA' in ui['d4']['title'] and 'YEAR 4' in ui['d4']['title']


def test_d10_success_renders_evaluates_and_clears_old_temporary_state(ui):
    d4 = ui['d4']
    assert d4['newRow'] is True and d4['oldRowGone'] is True
    assert d4['selectedLeft'] is False                           # old selections/locks gone
    assert d4['modalHidden'] is True and d4['csp'].startswith('CSP PASSED')
    assert d4['btnDisabled'] is False and 'GENERATE SCHEDULE' in d4['btnLabel']


def test_d11_failure_does_not_leave_the_page_stuck(ui):
    d11 = ui['d11']
    assert d11['urls'] == ['/api/schedule/retrieve-previous', '/api/schedule/generate']
    assert d11['modalTitle'] == 'Generation Failed'
    assert d11['btnDisabled'] is False and d11['keptDisplay'] is True
