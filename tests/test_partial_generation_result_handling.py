"""
Fix #6 — partial generation result handling.

Backend: /api/schedule/generate classifies every result into
COMPLETE_SUCCESS / PARTIAL_RESULT / TRUE_FAILURE from the canonical
evaluation (complete AND zero blocking hard violations), reports advisories
(SC9/HC_SPEC, severity 'warning') separately, and /api/schedule/approve
rejects an incomplete or hard-violating schedule with SCHEDULE_NOT_APPROVABLE.
Save Draft reports hard-constraint problems instead of refusing the draft.

The UI side (dialog, Discard/Review, approval gate, recovery) runs the real
page script under Node: tests/js/partial_result_harness.js.
"""
import inspect
import json
import pathlib
import shutil
import subprocess
from datetime import time

import pytest

import app as app_module
from conftest import requires_db
from test_reference_validation_and_progressive_regen import _row as _base_row


def _row(**over):
    # A hard-constraint-clean class (the shared fixture's 7:30 AM slot is an
    # HC2 violation for this designee).
    return _base_row(**dict({'start_time': time(9, 0), 'end_time': time(12, 0),
                             'time': '9:00 AM - 12:00 PM', 'duration_hrs': 3.0}, **over))

ADVISORY = {'rule': 'HC_SPEC', 'severity': 'warning', 'subject': 'GEED 002',
            'detail': 'Magtibay, Joel may not match the expected specialization for GEED 002.'}


class _FakeSolver:
    def __init__(self, rows, status='COMPLETE_VALID', violations=None, error=None):
        self.rows, self.status, self.violations, self.error = rows, status, violations or [], error

    def __call__(self, *a, **k):
        if self.status == 'GENERATION_ERROR':
            return {'success': False, 'result_status': 'GENERATION_ERROR', 'error': self.error or 'boom'}
        return {'success': self.status in ('COMPLETE_VALID', 'PARTIAL_VALID'), 'result_status': self.status,
                'schedule_data': [dict(r) for r in self.rows], 'violations': list(self.violations),
                'conflict_count': 0, 'incomplete_count': 0, 'completion_rate': 100.0, 'error': self.error}


def _generate(monkeypatch, solver):
    monkeypatch.setattr(app_module.scheduler_engine, 'generate_draft', solver)
    monkeypatch.setattr(app_module, '_check_cross_schedule_conflicts', lambda *a, **k: ([], 0))
    return app_module.app.test_client().post('/api/schedule/generate', json={
        'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627',
        'curriculum': '2022-2023'}).get_json()


# ── Result classification ───────────────────────────────────────────────────

@requires_db
def test_1_complete_generation_is_complete_success(monkeypatch):
    body = _generate(monkeypatch, _FakeSolver([_row()]))
    assert body['result_state'] == 'COMPLETE_SUCCESS', body.get('evaluation')
    assert body['evaluation']['eligibleForApproval'] is True
    assert body['incomplete_components'] == [] and body['blocking_violations'] == []


@requires_db
def test_2_tba_room_is_a_partial_result(monkeypatch):
    body = _generate(monkeypatch, _FakeSolver(
        [_row(), _row(subject_code='GEED 003', room_id=None, room='TBA', start_time=time(13, 30),
                      end_time=time(16, 30), time='1:30 PM - 4:30 PM')], status='PARTIAL_VALID'))
    assert body['result_state'] == 'PARTIAL_RESULT'
    assert [e['subject_code'] for e in body['incomplete_components']] == ['GEED 003']
    assert len(body['schedule_data']) == 2                       # the usable result is returned


@requires_db
def test_3_hard_violation_with_usable_result_is_a_partial_result(monkeypatch):
    # Two classes in the same room at the same time -> HC11 (and the section overlap).
    body = _generate(monkeypatch, _FakeSolver(
        [_row(), _row(subject_code='GEED 003', faculty_id='89128', instructor='Bulfa, Ronaldo')],
        status='INVALID_RESULT', error='One or more retained assignments still violate a hard constraint'))
    assert body['result_state'] == 'PARTIAL_RESULT'
    assert body['success'] is False and body['schedule_data']
    assert body['blocking_violations'] and body['evaluation']['hardViolationCount'] > 0


@requires_db
def test_4_true_failure_has_no_usable_result(monkeypatch):
    resp_body = _generate(monkeypatch, _FakeSolver([], status='GENERATION_ERROR', error='solver crashed'))
    assert resp_body['result_state'] == 'TRUE_FAILURE' and 'schedule_data' not in resp_body


@requires_db
def test_5_sc9_advisory_alone_is_complete_success_and_approvable(monkeypatch):
    body = _generate(monkeypatch, _FakeSolver([_row()], violations=[ADVISORY]))
    assert body['result_state'] == 'COMPLETE_SUCCESS'
    assert body['advisories'] == [ADVISORY]
    assert body['blocking_violations'] == [] and body['evaluation']['eligibleForApproval'] is True


@requires_db
def test_load_over_limit_with_other_sections_is_blocking_like_the_solver(monkeypatch):
    # The solver's HC9 counts hours already taught in other sections; the
    # evaluation (and so the approval gate) must too — the screenshot case
    # "PT load 30.0 hrs exceeds limit 12.0" was otherwise judged approvable.
    monkeypatch.setattr(app_module, '_other_sections_faculty_hours', lambda *a, **k: {'20123': 200.0})
    body = _generate(monkeypatch, _FakeSolver([_row()], status='INVALID_RESULT', violations=[ADVISORY]))
    assert body['result_state'] == 'PARTIAL_RESULT'
    assert [v['rule'] for v in body['blocking_violations']] == ['HC9']
    assert body['advisories'] == [ADVISORY]


@requires_db
def test_approve_counts_other_section_hours_for_the_load_rule(monkeypatch):
    from database import load_scheduler_config
    if not bool(load_scheduler_config().get('hc_publish_gate_enabled', 1)):
        pytest.skip('publish gate disabled in Settings')
    monkeypatch.setattr(app_module, '_other_sections_faculty_hours', lambda *a, **k: {'20123': 200.0})
    body = _approve([_row()]).get_json()
    assert body['error_code'] == 'SCHEDULE_NOT_APPROVABLE'
    assert [v['rule'] for v in body['violations']] == ['HC9']


# ── Backend approval gate ───────────────────────────────────────────────────

def _approve(rows):
    payload = [app_module._serialize_class(r) for r in rows]
    return app_module.app.test_client().post('/api/schedule/approve', json={
        'schedule_data': payload,
        'context': {'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627',
                    'curriculum': '2022-2023'}})


@requires_db
def test_16_approving_an_incomplete_schedule_is_rejected():
    # A nonexistent room is incomplete (Room "TBA" alone is publishable).
    resp = _approve([_row(), _row(subject_code='GEED 003', room_id='99999999', room='ZZ999',
                                  start_time=time(13, 30), end_time=time(16, 30), time='1:30 PM - 4:30 PM')])
    body = resp.get_json()
    assert resp.status_code == 400 and body['success'] is False
    assert body['error_code'] == 'SCHEDULE_NOT_APPROVABLE'
    assert body['incomplete_count'] == 1 and body['completion'] == 50.0
    assert body['incomplete_subjects'] == ['GEED 003']


@requires_db
def test_17_approving_with_a_hard_violation_is_rejected():
    from database import load_scheduler_config
    if not bool(load_scheduler_config().get('hc_publish_gate_enabled', 1)):
        pytest.skip('publish gate disabled in Settings')
    resp = _approve([_row(), _row(subject_code='GEED 003', faculty_id='89128', instructor='Bulfa, Ronaldo')])
    body = resp.get_json()
    assert resp.status_code == 400 and body['error_code'] == 'SCHEDULE_NOT_APPROVABLE'
    assert body['hard_violation_count'] > 0 and body['incomplete_count'] == 0


def test_approve_gate_ignores_advisories_and_keeps_its_gates():
    src = inspect.getsource(app_module.api_approve_schedule)
    assert "v.get('severity') != 'warning'" in src                  # SC9/HC_SPEC never blocks
    assert src.count('_not_approvable(') == 3                        # payload incomplete, hard, saved-draft incomplete


# ── Draft gate is not the approval gate ─────────────────────────────────────

def test_save_draft_reports_hard_problems_instead_of_refusing_the_draft():
    src = inspect.getsource(app_module.api_save_draft)
    assert 'must be resolved before saving as draft' not in src
    assert 'cannot save draft' not in src
    assert "'draft_warnings'" in src
    # Still validated through the same service, warnings (SC9) still separated.
    assert 'ConstraintService' in src and "v.get('severity') != 'warning'" in src


# ── UI (real page script under Node) ────────────────────────────────────────

@pytest.fixture(scope='module')
def ui():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    harness = pathlib.Path(__file__).resolve().parent / 'js' / 'partial_result_harness.js'
    out = subprocess.run([node, str(harness)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


REASON_BOTH = 'Complete all required assignments and resolve all hard-constraint violations before approval.'
REASON_INC = 'Complete all required schedule assignments before approval.'
REASON_HARD = 'Resolve all hard-constraint violations before approval.'


def test_ui_1_complete_generation_applies_without_the_issues_dialog(ui):
    assert ui['complete']['modalShown'] is False and ui['complete']['applied'] is True
    assert ui['complete']['approve'] == {'disabled': False, 'reason': ''}


def test_ui_2_partial_with_tba_shows_the_two_button_dialog(ui):
    m = ui['partialModal']
    assert m['title'] == 'Schedule Generated with Issues'
    assert (m['confirm'], m['cancel']) == ('Review Incomplete Schedule', 'Discard Result')
    assert m['tertiaryHidden'] is True
    assert '2 incomplete assignments' in m['message'] and '1 hard-constraint violation' in m['message']
    assert '1 advisory notice' in m['message'] and 'issue-advisory' in m['message']
    assert 'approval is disabled' in m['message']


def test_ui_3_partial_with_hard_violation_only_shows_the_dialog(ui):
    assert ui['hardOnlyModal']['title'] == 'Schedule Generated with Issues'


def test_ui_4_true_failure_shows_generation_failed_without_review(ui):
    f = ui['trueFailure']
    assert f['title'] == 'Generation Failed' and f['cancelHidden'] is True and f['confirm'] == 'OK'
    assert f['keptOld'] is True


def test_ui_5_advisory_only_is_not_a_failure_and_stays_approvable(ui):
    assert ui['advisoryOnly']['modalShown'] is False
    assert ui['advisoryOnly']['approve'] == {'disabled': False, 'reason': ''}


def test_ui_6_discard_keeps_the_previous_working_schedule(ui):
    d = ui['discard']
    assert d['oldRowShown'] is True and d['newRowShown'] is False
    assert d['stillSelected'] is True                 # temporary state of the old schedule untouched
    assert d['approve'] == ui['complete']['approve'] and d['csp'] == ui['complete']['csp']


def test_ui_7_review_applies_and_shows_incomplete_cells(ui):
    r = ui['review']
    assert r['newRowShown'] is True and r['oldRowShown'] is False
    assert r['tbaIncompleteFlags'] >= 1


def test_ui_8_review_refreshes_the_evaluation(ui):
    assert ui['review']['completion'] == 'COMPLETION 60% · 2 COMPONENTS INCOMPLETE'
    assert ui['review']['csp'].startswith('CSP FAILED')
    assert ui['review']['eligibleBadgeHidden'] is True


def test_ui_9_10_review_allows_draft_and_blocks_approval(ui):
    assert ui['review']['saveDraftDisabled'] is False
    assert ui['review']['approve'] == {'disabled': True, 'reason': REASON_BOTH}


def test_ui_11_to_15_approval_gate_matrix(ui):
    g = ui['gate']
    assert g['incompleteOnly'] == {'disabled': True, 'reason': REASON_INC}          # 11
    assert g['hardOnly'] == {'disabled': True, 'reason': REASON_HARD}               # 12
    assert g['both'] == {'disabled': True, 'reason': REASON_BOTH}                   # 13
    assert g['clean'] == {'disabled': False, 'reason': ''}                          # 14
    assert g['advisoryOnly'] == {'disabled': False, 'reason': ''}                   # 15
    assert g['highScoreButIncomplete']['disabled'] is True                          # not score-based


def test_ui_18_to_22_recovery_through_regenerate_selected(ui):
    rc = ui['recovery']
    assert rc['before']['approve']['disabled'] is True                              # 18
    assert rc['regenPayloadSelected'] == ['GEED 003', 'GEED 004']                   # 19
    assert rc['after']['completion'] == 'COMPLETION 100%'                           # 20
    assert rc['after']['csp'].startswith('CSP PASSED')
    assert rc['after']['approve'] == {'disabled': False, 'reason': ''}              # 21
    assert rc['after']['modalShown'] is False
    assert rc['before']['saveDraftDisabled'] is False and rc['after']['saveDraftDisabled'] is False  # 22
    assert rc['after']['selectedRows'] == []                                        # Fix #4 reset still applies


def test_ui_save_draft_of_a_partial_result_reports_open_issues(ui):
    s = ui['saveDraft']
    assert s['title'] == 'Draft Saved!' and 'cannot be approved until they are resolved' in s['message']
    assert s['request']['schedule_data'] and s['approveStillDisabled'] is True


def test_ui_rejected_approval_does_not_blindly_re_enable(ui):
    assert ui['rejectedApproval']['title'] == 'Publish Failed'
    assert ui['rejectedApproval']['approve']['disabled'] is True
