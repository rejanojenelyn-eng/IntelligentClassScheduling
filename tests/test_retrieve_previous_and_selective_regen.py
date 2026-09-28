"""
Retrieve Previous (historical_data only, immediately previous AY) and the
minimal lock-based "Re-generate Selected" flow.

Pure tests (no DB) cover the lock plumbing (align_user_locked_sessions,
_enforce_user_locks, the /api/schedule/generate guards). Frontend behavior is
tested by running the real page script under Node (tests/js/regen_lock_harness.js,
skipped if Node is absent). Real-DB tests
exercise the actual endpoints against the local database and are skipped when
it isn't reachable (see conftest.py).
"""
import pathlib
from datetime import time

import pytest

import app as app_module
import scheduler
from conftest import requires_db

JS_PATH = (pathlib.Path(__file__).resolve().parent.parent
           / "static" / "js" / "ACAD HEAD" / "scheduleGeneration.acad.js")
CTX = {'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627'}


def _client():
    return app_module.app.test_client()


def _gene(code, fid, room_id, start, end, days, class_type='Lecture', course='BEED'):
    return {
        'subject_code': code, 'class_type': class_type, 'course': course,
        'faculty_id': fid, 'instructor': f'Fac {fid}' if fid else None,
        'room_id': room_id, 'room': f'R{room_id}' if room_id else None, 'room_type': 'Lecture',
        'start_time': start, 'end_time': end, 'days_list': list(days), 'day': days[0] if days else None,
        'time': f'{start}-{end}', 'days': '/'.join(d[:3].upper() for d in days),
    }


# ── Lock plumbing (no DB) ──────────────────────────────────────────────────

def test_align_keeps_exact_keys_and_matches_historical_rows_without_class_type():
    subjects = [
        {'subjectcode': 'GEED 002', 'offeringcode': 'BEED-A', 'lecturehours': 3, 'laboratoryhours': 0},
        {'subjectcode': 'IT 101', 'offeringcode': 'BSIT', 'lecturehours': 2, 'laboratoryhours': 3},
    ]
    exact = {'subject_code': 'IT 101', 'class_type': 'Lab', 'course': 'BSIT', 'lock': {'room': True}}
    hist = {'subject_code': 'GEED 002', 'course': 'BEED', 'lock': {'faculty': True}}  # no class_type
    out = scheduler.align_user_locked_sessions([exact, hist], subjects)
    assert out[('IT 101', 'Lab', 'BSIT')] is exact
    assert out[('GEED 002', 'Lecture', 'BEED-A')] is hist
    # A lock entry is bound to one gene only — IT 101's Lecture gene has no lock.
    assert ('IT 101', 'Lecture', 'BSIT') not in out


def test_enforce_user_locks_restores_exact_values_and_leaves_unlocked_fields():
    sched = scheduler.IntelligentScheduler.__new__(scheduler.IntelligentScheduler)
    original = _gene('ELED 116', '17179', 18, time(15), time(18), ['Wednesday'])
    lock = dict(original, lock={'faculty': True, 'room': True, 'schedule': False})
    gene = _gene('ELED 116', '99999', 7, time(19, 30), time(21), ['Tuesday', 'Friday'])
    locked_parts = {('ELED 116', 'Lecture', 'BEED'): lock}
    sched._enforce_user_locks([gene], locked_parts)
    assert (gene['faculty_id'], gene['room_id']) == ('17179', 18)          # locked: restored
    assert gene['start_time'] == time(19, 30) and gene['days_list'] == ['Tuesday', 'Friday']  # unlocked: kept


def test_enforce_user_locks_keeps_an_empty_locked_value_and_ignores_cbr_locks():
    sched = scheduler.IntelligentScheduler.__new__(scheduler.IntelligentScheduler)
    # Unselected row whose faculty was TBA: must stay TBA, not be filled in.
    user = dict(_gene('GEED 001', None, 18, time(18), time(21), ['Monday']),
                lock={'faculty': True, 'room': True, 'schedule': True})
    cbr = dict(_gene('GEED 007', '1', 1, time(9), time(12), ['Monday']),
               lock={'faculty': True, 'room': True, 'schedule': True}, source_case='X')
    g1 = _gene('GEED 001', '555', 18, time(18), time(21), ['Monday'])
    g2 = _gene('GEED 007', '2', 2, time(13), time(16), ['Tuesday'])
    sched._enforce_user_locks([g1, g2], {('GEED 001', 'Lecture', 'BEED'): user,
                                         ('GEED 007', 'Lecture', 'BEED'): cbr})
    assert g1['faculty_id'] is None
    assert g2['faculty_id'] == '2'  # CBR locks stay releasable — untouched here


def test_all_locked_selection_is_rejected_without_running_the_solver(monkeypatch):
    called = []
    monkeypatch.setattr(app_module.scheduler_engine, 'generate_draft',
                        lambda *a, **k: called.append(1) or {})
    rows = [dict(_gene('GEED 002', '20123', 18, '09:00', '12:00', ['Monday']),
                 time='9:00 AM - 12:00 PM', selected=True,
                 lock={'faculty': True, 'room': True, 'schedule': True})]
    resp = _client().post('/api/schedule/generate', json=dict(CTX, locked_sessions=rows))
    assert resp.status_code == 400
    assert resp.get_json()['error'] == 'Unlock at least one field to regenerate.'
    assert not called


def test_infeasible_selected_row_reports_it_and_returns_no_schedule(monkeypatch):
    fake_result = {
        'success': True, 'result_status': 'PARTIAL_VALID', 'violations': [], 'conflict_count': 0,
        'schedule_data': [dict(_gene('GEED 002', '20123', None, time(9), time(12), ['Monday']),
                               incomplete=True, incomplete_reason=['HC11: no free room'])],
        'incomplete_count': 1, 'completion_rate': 0.0,
    }
    monkeypatch.setattr(app_module.scheduler_engine, 'generate_draft', lambda *a, **k: fake_result)
    monkeypatch.setattr(app_module, '_check_cross_schedule_conflicts', lambda *a, **k: ([], 0))
    rows = [dict(_gene('GEED 002', '20123', 18, '09:00', '12:00', ['Monday']),
                 time='9:00 AM - 12:00 PM', selected=True,
                 lock={'faculty': True, 'room': False, 'schedule': True})]
    body = _client().post('/api/schedule/generate', json=dict(CTX, locked_sessions=rows)).get_json()
    assert body['success'] is False
    assert body['result_status'] == 'REGENERATION_INFEASIBLE'
    # The generic "Try unlocking..." text was replaced: failure only comes after
    # every AUTO-kept field was released, and names the actual reason.
    assert body['error'] == ('GEED 002 could not be resolved because no valid faculty, room, and time '
                             'combination is currently available under the scheduling constraints.')
    assert 'Try unlocking' not in body['error']
    assert 'schedule_data' not in body


# ── Frontend behavior: the REAL page script run under Node (stub DOM) ────────
# tests/js/regen_lock_harness.js drives scheduleGeneration.acad.js itself —
# checking rows, clicking lock icons, Select All / Select Incomplete, and
# capturing the actual /api/schedule/generate payload.

UNLOCKED = {'faculty': 'is-unlocked', 'schedule': 'is-unlocked', 'room': 'is-unlocked', 'icons': 3}
NO_ICONS = {'faculty': None, 'schedule': None, 'room': None, 'icons': 0}
FIXED = {'selected': False, 'lock': {'faculty': True, 'room': True, 'schedule': True}}


@pytest.fixture(scope='module')
def ui():
    import json, shutil, subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    harness = pathlib.Path(__file__).resolve().parent / 'js' / 'regen_lock_harness.js'
    out = subprocess.run([node, str(harness)], capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_ui_unselected_row_shows_no_lock_icons(ui):
    assert ui['unselected'] == NO_ICONS


def test_ui_1_checking_a_row_starts_all_three_unlocked(ui):
    assert ui['checked'] == UNLOCKED


def test_ui_2_check_and_regenerate_sends_all_three_unlocked(ui):
    assert ui['payloadDefault']['ELED 116'] == {
        'selected': True, 'lock': {'faculty': False, 'room': False, 'schedule': False}}


def test_ui_3_locking_instructor_preserves_only_instructor(ui):
    assert ui['lockedInstructor'] == dict(UNLOCKED, faculty='is-locked')
    assert ui['payloadInstructor']['ELED 116']['lock'] == {'faculty': True, 'room': False, 'schedule': False}


def test_ui_4_locking_instructor_and_room_leaves_only_time_days(ui):
    assert ui['payloadInstructorRoom']['ELED 116']['lock'] == {'faculty': True, 'room': True, 'schedule': False}


def test_ui_5_all_three_locked_sends_nothing_and_says_so(ui):
    assert ui['allLockedState'] == {'faculty': 'is-locked', 'schedule': 'is-locked', 'room': 'is-locked', 'icons': 3}
    assert ui['payloadAllLocked'] is None                       # no request made
    assert 'Unlock at least one field to regenerate.' in ui['allLockedToast']


def test_ui_6_7_uncheck_clears_state_and_recheck_starts_unlocked_again(ui):
    assert ui['afterUncheck'] == NO_ICONS
    assert ui['afterRecheck'] == UNLOCKED                       # stale locks not restored


def test_ui_8_select_all_initializes_every_row_unlocked(ui):
    assert ui['selectAll'] == [UNLOCKED, UNLOCKED, UNLOCKED]


def test_ui_9_select_incomplete_initializes_incomplete_rows_unlocked(ui):
    assert ui['selectIncomplete'] == [NO_ICONS, NO_ICONS, UNLOCKED]  # only GEED 002 is incomplete


def test_ui_10_unselected_rows_are_always_sent_fully_locked(ui):
    for key in ('payloadDefault', 'payloadInstructor', 'payloadInstructorRoom'):
        assert ui[key]['GEED 001'] == FIXED and ui[key]['GEED 002'] == FIXED


def _hist_cells(panel):
    import re
    return re.findall(r'(\d+%|N/A)</span>', panel['breakdown'])[-3:]


def test_ui_evaluation_refreshes_after_regeneration_that_creates_a_conflict(ui):
    assert ui['evalBaseline']['score'] == '91%'
    assert ui['evalAfterConflict']['score'] == '77%'
    assert ui['evalAfterConflict']['csp'].startswith('CSP FAILED · 2')
    assert _hist_cells(ui['evalAfterConflict']) == ['63%', '63%', '63%']


def test_ui_evaluation_refreshes_after_regeneration_that_resolves_it_with_zero_conflicts(ui):
    assert ui['evalAfterResolve']['score'] == '100%'
    assert ui['evalAfterResolve']['csp'].startswith('CSP PASSED · 0')
    assert _hist_cells(ui['evalAfterResolve']) == ['100%', '100%', '100%']


def test_ui_evaluation_is_recomputed_not_kept_when_response_has_none(ui):
    call = ui['evalFallbackCall']
    assert call and call['body']['acad_year'] == 'AY2627' and call['body']['section'] == '464'
    assert ui['evalAfterFallback']['score'] == '64%'           # not the previous 100%
    assert _hist_cells(ui['evalAfterFallback']) == ['43%', '43%', '43%']


def test_ui_loading_a_draft_re_evaluates(ui):
    assert ui['evalDraftCall'] is not None
    assert ui['evalAfterDraft']['score'] == '55%'


# ── Conflict panel + conflict-aware selection (harness section "cp*") ────────

L_, U_ = 'is-locked', 'is-unlocked'
_NO = {'faculty': None, 'schedule': None, 'room': None, 'icons': 0}


def _locks(faculty, schedule, room):
    return {'faculty': faculty, 'schedule': schedule, 'room': room, 'icons': 3}


# ELED 116: faculty clash (HC10) + time block (HC6) -> Instructor + Time/Days
# GEED 001: room clash (HC11) AND Room incomplete   -> all three (incomplete rows start unlocked)
# GEED 002: faculty load (HC9, its own faculty)     -> Instructor only
# NSTP 001: only a load conflict of ANOTHER faculty -> not a conflict row
CONFLICT_DEFAULTS = {
    'ELED 116': _locks(U_, U_, L_),
    'GEED 001': _locks(U_, U_, U_),
    'GEED 002': _locks(U_, L_, L_),
}


def test_cp_four_plus_conflicts_start_collapsed_and_toggle_in_place(ui):
    c = ui['cpCollapsed']
    assert c['hidden'] is False and c['title'] == '5 Schedule Conflicts'
    assert c['listHidden'] is True and c['toggle'] == 'Show Details' and c['items'] == 5
    e = ui['cpExpanded']
    assert e['listHidden'] is False and e['toggle'] == 'Hide Details'
    assert ui['cpCollapsedAgain']['listHidden'] is True
    assert '&lt;b&gt;invalid&lt;/b&gt;' in e['listHtml']          # conflict text is escaped


def test_cp_one_to_three_conflicts_start_expanded(ui):
    for key, n in (('cpThree', 3), ('cpAfterRegenOne', 1)):
        p = ui[key]
        assert p['hidden'] is False and p['items'] == n and p['listHidden'] is False
        assert p['toggle'] == 'Hide Details'
    assert ui['cpAfterRegenOne']['title'] == '1 Schedule Conflict'


def test_cp_row_indicators_come_from_the_same_conflict_list(ui):
    assert ui['cpFlags'] == {'ELED 116': 1, 'GEED 001': 1, 'GEED 002': 1, 'NSTP 001': 0}


def test_cp_select_conflict_rows_sets_conflict_locks_and_does_not_regenerate(ui):
    assert ui['cpSelectConflictRows'] == dict(CONFLICT_DEFAULTS, **{'NSTP 001': _NO})
    assert ui['cpSelectConflictCalls'] == 0


def test_cp_regenerate_sends_conflict_locks_and_unselected_rows_fully_locked(ui):
    p = ui['cpRegenPayload']
    assert p['ELED 116'] == {'selected': True, 'lock': {'faculty': False, 'room': True, 'schedule': False}}
    assert p['GEED 001'] == {'selected': True, 'lock': {'faculty': False, 'room': False, 'schedule': False}}
    assert p['GEED 002'] == {'selected': True, 'lock': {'faculty': False, 'room': True, 'schedule': True}}
    assert p['NSTP 001'] == FIXED


def test_cp_individual_check_uses_conflict_locks_and_clean_row_starts_unlocked(ui):
    assert ui['cpIndividual'] == dict(CONFLICT_DEFAULTS, **{'NSTP 001': UNLOCKED})
    assert ui['cpManualToggle'] == _locks(U_, U_, U_)              # still manually changeable
    assert ui['cpRecheck'] == CONFLICT_DEFAULTS['ELED 116']        # re-check -> defaults again


def test_cp_select_all_uses_conflict_locks(ui):
    assert ui['cpSelectAll'] == dict(CONFLICT_DEFAULTS, **{'NSTP 001': UNLOCKED})


def test_cp_select_incomplete_unlocks_incomplete_and_conflicting_components_only(ui):
    # GEED 001: Room incomplete + room clash -> all three unlocked (incomplete row).
    assert ui['cpSelectIncomplete'] == {'ELED 116': _NO, 'GEED 001': _locks(U_, U_, U_),
                                        'GEED 002': _NO, 'NSTP 001': _NO}


def test_cp_panel_hides_and_flags_clear_when_all_conflicts_resolved(ui):
    assert ui['cpResolved']['hidden'] is True
    assert ui['cpResolvedFlags'] == 0


# ── EDUC 021/022/023 (harness section "rs*"): conflict ∪ incomplete ──────────
# EDUC 021: Room TBA only · EDUC 022: TBA + faculty clash + time block ·
# EDUC 023: TBA + faculty clash (which involves instructor, day and time).
EDUC_DEFAULTS = {
    'EDUC 021': _locks(U_, U_, U_),       # incomplete rows start all unlocked
    'EDUC 022': _locks(U_, U_, U_),
    'EDUC 023': _locks(U_, U_, U_),
}


def test_rs_all_tba_rows_are_automatically_selected_without_regenerating(ui):
    assert ui['rsAuto'] == dict(EDUC_DEFAULTS, **{'NSTP 001': _NO})   # conflict-free NSTP not selected
    assert ui['rsAutoGenerateCalls'] == 0


def test_incomplete_row_auto_selected_then_manual_uncheck_sticks(ui):
    assert ui['autoFirst']['GEED2'] == UNLOCKED        # flagged incomplete, component unknown -> all three
    assert ui['autoFirst']['ELED'] == NO_ICONS         # complete row: not auto-selected
    assert ui['autoFirst']['generateCalls'] == 0
    assert ui['afterUserUncheck'] == NO_ICONS


def test_rs_tba_is_incomplete_with_banner_and_cell_markers(ui):
    assert ui['rsBanner'] == {'hidden': False, 'text': '3 Incomplete Components', 'selectHidden': False}
    assert ui['rsIncompleteCellFlags'] == {'EDUC 021': 1, 'EDUC 022': 1, 'EDUC 023': 1, 'NSTP 001': 0}


def test_rs_select_conflict_rows_also_unlocks_the_incomplete_tba_room(ui):
    # EDUC 021 (incomplete only, unchecked by the user) stays unchecked: the
    # conflict button selects conflict rows and doesn't fight a manual uncheck.
    assert ui['rsViaConflictRows'] == {'EDUC 021': _NO, 'EDUC 022': EDUC_DEFAULTS['EDUC 022'],
                                       'EDUC 023': EDUC_DEFAULTS['EDUC 023'], 'NSTP 001': _NO}


def test_rs_tba_room_cannot_be_locked(ui):
    assert ui['rsRoomOrigin'] == 'unresolved'
    assert ui['rsRoomAfterClick'] == U_


def test_rs_select_incomplete_also_unlocks_conflicting_components(ui):
    assert ui['rsViaSelectIncomplete'] == dict(EDUC_DEFAULTS, **{'NSTP 001': _NO})


def test_rs_individual_check_uses_the_same_combined_state(ui):
    assert ui['rsIndividual23'] == EDUC_DEFAULTS['EDUC 023']


def test_rs_auto_kept_vs_user_locked_and_payload(ui):
    assert ui['rsAutoOrigin'] == 'open'     # incomplete row: starts unlocked, nothing auto-kept
    assert ui['rsUserOrigin'] == 'user'     # after the Academic Head locks it
    p = ui['rsPayload']
    assert p['EDUC 021'] == {'selected': True, 'lock': {'faculty': True, 'room': False, 'schedule': False},
                             'user_lock': {'faculty': True, 'room': False, 'schedule': False}}
    assert p['EDUC 022']['lock'] == {'faculty': False, 'room': False, 'schedule': False}
    assert p['EDUC 023']['lock'] == {'faculty': False, 'room': False, 'schedule': False}
    # Unselected rows: fully locked.
    assert p['NSTP 001'] == {'selected': False, 'lock': {'faculty': True, 'room': True, 'schedule': True},
                             'user_lock': {'faculty': False, 'room': False, 'schedule': False}}


def test_rs_after_regeneration_ui_state_follows_the_applied_result(ui):
    assert ui['rsToast'] == 'Also regenerated Instructor for EDUC 023 to resolve it.'
    assert ui['rsBannerAfter'] == {'hidden': False, 'text': '1 Incomplete Component'}
    assert ui['rsConflictPanelAfter'] is True
    assert ui['rsAllResolved'] == {'banner': True, 'panel': True, 'cellFlags': 0}


def test_no_second_incomplete_popup_and_button_renamed():
    js = JS_PATH.read_text(encoding='utf-8')
    tpl = (JS_PATH.parents[3] / 'templates' / 'academic' / 'scheduleGeneration.html').read_text(encoding='utf-8')
    assert "'Incomplete Components'" not in js           # the second modal is gone
    assert "'Schedule Generated with Issues'" in js      # the first one stays (renamed by Fix #6)
    assert 'Select Incomplete</button>' in tpl and 'Select All Incomplete' not in tpl
    assert "getElementById('incompleteBanner').classList.add('hidden')\" style" not in tpl   # no ×


def test_frontend_static_contract():
    js = JS_PATH.read_text(encoding='utf-8')
    assert "const REGEN_FIELDS = ['faculty', 'schedule', 'room'];" in js
    assert js.count("cellWithPreserve(") == 3  # instructor, time and room cells only
    # Retrieve Previous: plain success toast, no fallback warning dialog.
    assert "Previous AY schedule retrieved from ${data.retrieved_from.ay_label}." in js
    assert "Exact Reuse Unavailable" not in js


# ── Real-DB: Retrieve Previous ──────────────────────────────────────────────

@requires_db
def test_a_retrieves_immediately_previous_ay_from_historical_data():
    resp = _client().post('/api/schedule/retrieve-previous', json=CTX)
    body = resp.get_json()
    assert resp.status_code == 200 and body['success'] is True
    assert body['retrieved_from']['acadYear'] == 'AY2526'
    assert body['retrieved_from']['ay_label'] == 'AY202526'
    assert body['retrieved_from']['source'] == 'historical'
    assert 'exact_reuse' not in body
    assert body['schedule_data']


@requires_db
def test_b_no_previous_ay_schedule_is_reported_and_older_years_are_not_searched():
    # AY2526 has a Summer semester record but no BEED Year 1 Summer rows.
    resp = _client().post('/api/schedule/retrieve-previous', json=dict(CTX, term='C'))
    body = resp.get_json()
    assert resp.status_code == 404
    assert body['error_code'] == 'NO_PREVIOUS_SCHEDULE'
    assert body['error'] == 'No previous AY schedule was found for BEED Year 1, Summer, AY202526.'
    assert 'schedule_data' not in body


@requires_db
def test_c_historical_bare_times_use_school_day_convention_on_retrieval_only():
    body = _client().post('/api/schedule/retrieve-previous', json=CTX).get_json()
    by_code = {r['subject_code']: r for r in body['schedule_data']}
    assert by_code['ELED 105']['time'] == '3:00 PM - 6:00 PM'    # stored "3:00 - 6:00"
    assert by_code['GEED 002']['time'] == '9:00 AM - 12:00 PM'   # stored "9:00 - 12:00"
    assert by_code['PATHFIT 1']['time'] == '5:00 PM - 7:00 PM'   # stored "5:00 - 7:00"
    assert by_code['ELED 105']['days_list'] == ['Monday']
    # The strict scheduler parser is NOT weakened.
    assert scheduler.parse_historical_day_time_verbose('M', '3:00 - 6:00')[3] == 'unresolved'


@requires_db
def test_k_retrieved_schedule_is_validated_against_current_constraints():
    body = _client().post('/api/schedule/retrieve-previous', json=CTX).get_json()
    # Faculty/room resolved to current records so the current-AY checks run.
    assert all(r['faculty_id'] and r['room_id'] for r in body['schedule_data'])
    ev = body['evaluation']
    # PATHFIT 1's legacy 5:00 PM start is not a standard block this AY (HC6).
    assert ev['cspPassed'] is False and ev['hardViolationCount'] >= 1
    assert any('PATHFIT 1' in (v.get('detail') or '') for v in body['violations'])


# ── Real-DB: Re-generate Selected on a retrieved schedule ───────────────────

_PLUCK = ['subject_code', 'class_type', 'course', 'description', 'lec_hours', 'lab_hours', 'units',
          'faculty_id', 'instructor', 'room_id', 'room', 'room_type', 'days_list', 'day', 'time', 'days']
_FIELDS = {'faculty': ('faculty_id', 'instructor'), 'room': ('room_id', 'room'), 'schedule': ('time', 'days')}
_LOCKED = {'faculty': True, 'room': True, 'schedule': True}


@requires_db
def test_2_3_4_10_selected_rows_solved_together_unselected_rows_untouched():
    client = _client()
    rows = client.post('/api/schedule/retrieve-previous', json=CTX).get_json()['schedule_data']
    locks = {
        'ELED 116': {'faculty': False, 'schedule': False, 'room': False},  # 2: default, all may change
        'GEED 002': {'faculty': True, 'schedule': False, 'room': False},   # 3: Instructor preserved
        'PATHFIT 1': {'faculty': True, 'schedule': False, 'room': True},   # 4: only Time/Days
    }
    payload = [dict({k: r[k] for k in _PLUCK if k in r}, row_key=r['subject_code'],
                    selected=r['subject_code'] in locks, lock=locks.get(r['subject_code'], _LOCKED))
               for r in rows]
    body = client.post('/api/schedule/generate', json=dict(
        CTX, curriculum='2022-2023', section='', locked_sessions=payload)).get_json()
    assert body['success'] is True, body.get('error')

    after = {g['subject_code']: g for g in body['schedule_data']}
    for r in rows:
        g = after[r['subject_code']]
        lock = locks.get(r['subject_code'], _LOCKED)   # J: unselected rows fully locked
        for flag, fields in _FIELDS.items():
            if lock[flag]:
                for f in fields:
                    assert str(g[f]) == str(r[f]), (r['subject_code'], f, r[f], g[f])
    # Unlocking PATHFIT 1's Time/Days lets the solver clear its HC6 violation,
    # and the full evaluation is re-run on the result.
    assert body['evaluation'] and 'overallScore' in body['evaluation']
    assert not any('PATHFIT 1' in (v.get('detail') or '') for v in body['violations'])
