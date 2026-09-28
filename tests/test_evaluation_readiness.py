"""
Quality score vs completeness vs approval eligibility.

Root cause fixed here: the 60/30/10 score is a QUALITY score, but its criteria
only read HC5, HC7, HC9-HC13 and HC17 — a violation of HC1-HC4 (teaching
windows), HC6 (standard time grid), HC8 (night limit) or HC14 changed CSP
status and eligibility but no criterion, so e.g. BSA 3 showed "Overall 100%"
next to "CSP FAILED" and "Completion 86%". Every violation-emitting hard rule
now maps to a criterion (app._EVAL_CRITERION_RULES), and the page labels the
figure as a quality score and shows approval readiness beside it.

UI parts run the real page script under Node (tests/js/evaluation_readiness_harness.js).
"""
import json
import pathlib
import shutil
import subprocess
from datetime import time

import pytest

import app as app_module
from conftest import requires_db
from constraints.hard_constraints import VIOLATION_EMITTING_IDS
from test_reference_validation_and_progressive_regen import _row as _base_row


def _row(**over):
    return _base_row(**dict({'start_time': time(9, 0), 'end_time': time(12, 0),
                             'time': '9:00 AM - 12:00 PM', 'duration_hrs': 3.0}, **over))


CLEAN = [_row(subject_code='GEED 002'),
         _row(subject_code='GEED 003', start_time=time(13, 30), end_time=time(16, 30),
              time='1:30 PM - 4:30 PM')]
OFF_GRID = dict(start_time=time(13, 0), end_time=time(16, 0), time='1:00 PM - 4:00 PM')   # HC6
TBA_ROOM = dict(room_id=None, room='TBA')


def _eval(rows):
    return app_module._compute_schedule_evaluation(
        [app_module._serialize_class(r) for r in rows], 'BEED', 1, 'A',
        acad_year='AY2627', curriculum='2022-2023')


def test_every_violation_emitting_hard_rule_lowers_some_quality_criterion():
    mapped = {r for rules in app_module._EVAL_CRITERION_RULES.values() for r in rules}
    assert set(VIOLATION_EMITTING_IDS) <= mapped, set(VIOLATION_EMITTING_IDS) - mapped


@requires_db
def test_time_grid_violation_is_reflected_in_the_quality_criteria():
    ev = _eval([CLEAN[0], dict(CLEAN[1], **OFF_GRID)])
    assert ev['hardViolationCount'] == 1 and ev['cspPassed'] is False            # 1:00 PM start is off grid
    assert ev['categories']['constraintCompliance']['criteria']['schedulePairingDistribution'] == 50.0
    assert ev['overallScore'] < 100
    assert ev['scoreKind'] == 'quality'
    assert 'HC6' in ev['constraintRules']['schedulePairingDistribution']


@requires_db
def test_1_incomplete_without_violations_is_not_approvable():
    ev = _eval([CLEAN[0], dict(CLEAN[1], **TBA_ROOM)])
    assert ev['hardViolationCount'] == 0 and ev['completionRate'] == 50.0
    assert ev['eligibleForApproval'] is False


@requires_db
def test_2_incomplete_with_a_violation_reflects_both_and_is_not_approvable():
    ev = _eval([dict(CLEAN[0], **OFF_GRID), dict(CLEAN[1], **TBA_ROOM)])
    assert ev['hardViolationCount'] > 0 and ev['completionRate'] == 50.0
    assert ev['categories']['constraintCompliance']['criteria']['schedulePairingDistribution'] < 100
    assert ev['eligibleForApproval'] is False


@requires_db
def test_3_complete_with_a_violation_is_not_approvable():
    ev = _eval([CLEAN[0], dict(CLEAN[1], **OFF_GRID)])
    assert ev['completionRate'] == 100.0 and ev['eligibleForApproval'] is False


@requires_db
def test_4_high_historical_match_does_not_make_an_incomplete_schedule_approvable():
    # Live BSA 3 retrieval (the reported case): historical match 100%, 1 row incomplete.
    from database import query_db
    sid = query_db("""SELECT s.sectionid FROM sections s JOIN program_yearlevel p
                      ON p.programyearlevelid = s.programyearlevelid
                      WHERE p.programcode = 'BSA' AND p.yearlevel = 3 AND p.academicyearid = 'AY2627'
                      LIMIT 1""", one=True)
    if not sid:
        pytest.skip('no BSA 3 section')
    body = app_module.app.test_client().post('/api/schedule/retrieve-previous', json={
        'program': 'BSA', 'yearLevel': 3, 'term': 'A', 'acadYear': 'AY2627',
        'section': str(sid['sectionid']), 'curriculum': '2022-2023'}).get_json()
    if not body.get('success'):
        pytest.skip('no BSA 3 history')
    ev = body['evaluation']
    hist = ev['categories']['recommendationQuality']['criteria']
    if ev['incompleteCount'] == 0 or hist.get('historicalFacultyMatch') != 100.0:
        pytest.skip('BSA 3 history no longer reproduces the reported case')
    assert ev['completionRate'] < 100 and ev['eligibleForApproval'] is False
    # ACCO 304's off-grid 1:00 PM start now lowers a quality criterion.
    if any(c['rule'] == 'HC6' for c in ev['conflicts']):
        assert ev['categories']['constraintCompliance']['criteria']['schedulePairingDistribution'] < 100
        assert ev['overallScore'] < 100


@requires_db
def test_8_only_complete_and_hard_clean_is_approvable():
    ev = _eval(CLEAN)
    assert (ev['completionRate'], ev['hardViolationCount'], ev['eligibleForApproval']) == (100.0, 0, True)


@requires_db
def test_9_usable_but_incomplete_generation_is_a_partial_result(monkeypatch):
    from test_partial_generation_result_handling import _FakeSolver, _generate
    body = _generate(monkeypatch, _FakeSolver([CLEAN[0], dict(CLEAN[1], **TBA_ROOM)], status='PARTIAL_VALID'))
    assert body['result_state'] == 'PARTIAL_RESULT'
    assert body['evaluation']['completionRate'] < 100


@requires_db
def test_10_backend_approval_and_evaluation_use_the_same_completeness_result():
    rows = [CLEAN[0], dict(CLEAN[1], **TBA_ROOM)]
    ev = _eval(rows)
    assert [(e['subject_code'], e['components']) for e in ev['incomplete']] == [('GEED 003', ['room'])]
    assert ev['incompleteComponentCount'] == 1 and ev['incompleteCount'] == 1
    body = app_module.app.test_client().post('/api/schedule/approve', json={
        'schedule_data': [app_module._serialize_class(r) for r in rows],
        'context': {'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627',
                    'curriculum': '2022-2023'}}).get_json()
    assert body['error_code'] == 'SCHEDULE_NOT_APPROVABLE'
    assert body['incomplete_subjects'] == [e['subject_code'] for e in ev['incomplete']]
    assert body['completion'] == ev['completionRate']


# ── UI ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def ui():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    harness = pathlib.Path(__file__).resolve().parent / 'js' / 'evaluation_readiness_harness.js'
    out = subprocess.run([node, str(harness)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_ui_panel_separates_quality_completion_csp_and_eligibility(ui):
    p = ui['reviewed']['panel']
    assert p['score'] == '100%' and 'eval-good' not in p['scoreClass']       # not shown as "perfect"
    assert p['completion'] == 'COMPLETION 86% · 1 COMPONENT INCOMPLETE'
    assert p['csp'].startswith('CSP FAILED · 1 HARD-CONSTRAINT VIOLATION')
    assert p['eligibleBadgeHidden'] is True
    assert p['readinessHidden'] is False
    assert p['readiness'] == 'NOT ELIGIBLE FOR APPROVAL · 1 INCOMPLETE COMPONENT · 1 HARD-CONSTRAINT VIOLATION'


def test_ui_incomplete_banner_names_the_exact_row_and_field(ui):
    b = ui['reviewed']['banner']
    assert b['text'] == '1 Incomplete Component'
    assert b['items'] == ["ACCO 305 — Room: Room '122/TBA' is not a valid room record."]


def test_ui_review_leaves_the_working_tools_available(ui):
    t = ui['reviewed']['tools']
    assert t == {'saveDraft': False, 'approve': True, 'regenerate': False, 'manualEditor': False,
                 'selectConflictHidden': False, 'selectIncompleteHidden': False}


def test_ui_5_select_incomplete_selects_exactly_the_affected_rows_all_unlocked(ui):
    assert ui['selectIncomplete'] == {'ACCO 305': {'faculty': 'is-unlocked', 'schedule': 'is-unlocked',
                                                   'room': 'is-unlocked', 'icons': 3}}


def test_ui_6_7_8_regeneration_fixes_counts_and_enables_approval_only_when_both_zero(ui):
    r = ui['afterFixIncomplete']
    assert r['completion'] == 'COMPLETION 100%' and r['csp'].startswith('CSP FAILED · 1')
    assert r['approveDisabled'] is True and r['checked'] == []                      # 6: incomplete gone
    r2 = ui['afterFixViolation']
    assert r2['csp'].startswith('CSP PASSED') and r2['completion'] == 'COMPLETION 100%'   # 7
    assert r2['approveDisabled'] is False and r2['readinessHidden'] is True          # 8
    assert r2['eligibleBadgeHidden'] is False and 'eval-good' in r2['scoreClass']
    assert r2['bannerListAfter'] == ''                                               # no stale row names
