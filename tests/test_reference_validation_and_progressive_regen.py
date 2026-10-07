"""
Reference validation (present AND valid = resolved) and progressive
Re-generate Selected (AUTO-kept locks released only as needed; USER locks never).

- _unresolved_components: Room "TBA"/nonexistent, unknown faculty, off-grid time,
  invalid day and out-of-curriculum subject are INCOMPLETE; a valid room that
  clashes is a CONFLICT, not incomplete.
- The same definition is used by the evaluation (and therefore re-evaluation),
  Retrieve Previous and Approve.
- The progressive loop (api_generate_schedule) is exercised with a scripted
  solver so every round is deterministic, and against the real EDUC 022/023
  data from BEED 4 / AY2526 (all Room TBA; 022 + 023 double-booked).
"""
import copy
from datetime import time

import pytest

import app as app_module
from conftest import requires_db

FIELDS = ('faculty', 'schedule', 'room')


def _row(**over):
    r = {'subject_code': 'GEED 002', 'class_type': 'Lecture', 'course': 'BEED',
         'faculty_id': '20123', 'instructor': 'Magtibay, Joel', 'room_id': 18, 'room': 'LQ117',
         'start_time': time(7, 30), 'end_time': time(10, 30), 'days_list': ['Monday'], 'day': 'Monday',
         'time': '7:30 AM - 10:30 AM', 'days': 'MON'}
    r.update(over)
    return r


@pytest.fixture(scope='module')
def refs():
    r = app_module._load_reference_data('BEED', 1, 'A', '2022-2023')
    if r is None:
        pytest.skip('database not reachable')
    return r


# ── Reference validation ────────────────────────────────────────────────────

@requires_db
def test_valid_row_resolves(refs):
    assert app_module._unresolved_components(_row(), refs) == ([], [])


@requires_db
@pytest.mark.parametrize('over,component', [
    ({'room_id': None, 'room': 'TBA'}, 'room'),
    ({'room_id': 'TBA', 'room': 'TBA'}, 'room'),
    ({'room_id': 99999, 'room': 'LQ999'}, 'room'),            # present, but no such room
    ({'room_id': None, 'room': ''}, 'room'),
    ({'faculty_id': 'NOPE-1', 'instructor': 'Juan Dela Cruz'}, 'faculty'),
    ({'faculty_id': None, 'instructor': 'TBA'}, 'faculty'),
    ({'start_time': time(7, 15), 'end_time': time(10, 15)}, 'schedule'),   # not on the timeslot grid
    ({'start_time': None, 'end_time': None}, 'schedule'),
    ({'days_list': ['Funday'], 'day': 'Funday'}, 'schedule'),
    ({'start_time': time(10, 30), 'end_time': time(7, 30)}, 'schedule'),
    ({'subject_code': 'ABC 123'}, 'subject'),                  # not in BEED 1 / 1st sem curriculum
])
def test_present_but_invalid_values_are_unresolved(refs, over, component):
    comps, reasons = app_module._unresolved_components(_row(**over), refs)
    assert comps == [component] and reasons


@requires_db
def test_valid_room_that_clashes_is_a_conflict_not_incomplete():
    a = _row(subject_code='GEED 002')
    b = _row(subject_code='GEED 003', faculty_id='24102', instructor='Dotado, Conchita')  # same room/time
    ev = app_module._compute_schedule_evaluation([a, b], 'BEED', 1, 'A', acad_year='AY2627',
                                                 curriculum='2022-2023')
    assert ev['incomplete'] == []
    assert any(c['rule'] == 'HC11' for c in ev['conflicts'])


@requires_db
def test_evaluation_counts_tba_room_even_without_a_client_flag():
    ev = app_module._compute_schedule_evaluation(
        [_row(), _row(subject_code='GEED 003', faculty_id='24102', room_id=None, room='TBA',
                      start_time=time(13, 0), end_time=time(16, 0))],
        'BEED', 1, 'A', acad_year='AY2627', curriculum='2022-2023')
    assert ev['completionRate'] == 50.0 and ev['incompleteCount'] == 1
    assert [(e['subject_code'], e['components']) for e in ev['incomplete']] == [('GEED 003', ['room'])]
    assert ev['eligibleForApproval'] is False


@requires_db
def test_approve_rejects_nonexistent_references_but_accepts_tba_room_and_faculty():
    # Room / Faculty "TBA" (to be announced) are publishable; a nonexistent room / faculty is not.
    rows = [app_module._serialize_class(_row(room_id=None, room='TBA')),
            app_module._serialize_class(_row(subject_code='GEED 003', faculty_id='NOPE-1')),
            app_module._serialize_class(_row(subject_code='GEED 004', room_id='99999999', room='ZZ999')),
            app_module._serialize_class(_row(subject_code='GEED 005', faculty_id='TBA', instructor='TBA'))]
    resp = app_module.app.test_client().post('/api/schedule/approve', json={
        'schedule_data': rows,
        'context': {'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627'}})
    body = resp.get_json()
    assert resp.status_code == 400 and body['success'] is False
    assert body['incomplete_subjects'] == ['GEED 003', 'GEED 004']
    assert 'GEED 003 (Instructor)' in body['error'] and 'GEED 004 (Room)' in body['error']
    assert 'GEED 002' not in body['error'] and 'GEED 005' not in body['error']


# ── Progressive regeneration: scripted solver (deterministic rounds) ─────────

class _ScriptedSolver:
    """Stands in for generate_draft. 'A' always resolves (to a room that
    differs every round, so an unnecessary re-solve would be visible); 'B'
    resolves only once its listed fields are unlocked."""
    def __init__(self, b_needs):
        self.b_needs, self.calls = set(b_needs), []

    def __call__(self, program, year_level, term, curriculum, use_historical=False,
                 acad_year_id='', locked_sessions=None, seed=None, section_id=None):
        self.calls.append(copy.deepcopy(locked_sessions))
        out = []
        for ls in locked_sessions:
            g = {k: v for k, v in ls.items() if k not in ('lock', 'user_lock', 'selected', 'row_key')}
            lock = ls.get('lock') or {}
            if g['subject_code'] == 'A' and ls.get('selected') and not lock.get('room'):
                g.update(room_id=[19, 20, 21, 22, 23][len(self.calls) - 1], room='R')
            if g['subject_code'] == 'B' and ls.get('selected'):
                if 'never' not in self.b_needs and all(not lock.get(f) for f in self.b_needs):
                    g.update(room_id=18, room='LQ117')
                else:
                    g.update(room_id=None, room=None, incomplete=True, incomplete_reason=['no room'])
            out.append(g)
        return {'success': True, 'result_status': 'COMPLETE_VALID', 'schedule_data': out,
                'violations': [], 'conflict_count': 0, 'incomplete_count': 0, 'completion_rate': 100.0}


def _regen(monkeypatch, solver, rows):
    monkeypatch.setattr(app_module.scheduler_engine, 'generate_draft', solver)
    monkeypatch.setattr(app_module, '_check_cross_schedule_conflicts', lambda *a, **k: ([], 0))
    payload = [app_module._serialize_class(r) for r in rows]
    return app_module.app.test_client().post('/api/schedule/generate', json={
        'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627',
        'locked_sessions': payload}).get_json()


def _sel(code, lock, user=None, **over):
    return dict(_row(subject_code=code, **over), selected=True, row_key=code,
                lock=dict(zip(FIELDS, lock)), user_lock=dict(zip(FIELDS, user or (False,) * 3)))


def _fixed(code, **over):
    return dict(_row(subject_code=code, **over), selected=False, row_key=code,
                lock={f: True for f in FIELDS}, user_lock={f: False for f in FIELDS})


@requires_db
def test_progressive_release_frees_auto_kept_fields_until_resolved(monkeypatch):
    # B keeps Instructor + Time/Days (AUTO) and has Room TBA; it only resolves
    # once Time/Days is released too.
    solver = _ScriptedSolver(b_needs=('room', 'schedule'))
    rows = [_sel('A', (True, True, False)),
            _sel('B', (True, True, True), room_id=None, room='TBA', start_time=time(13, 0), end_time=time(16, 0)),
            _fixed('C', start_time=time(16, 30), end_time=time(19, 30))]
    body = _regen(monkeypatch, solver, rows)
    assert body['success'] is True
    assert body['released_locks'] == {'B': ['schedule']}             # Room was TBA: never kept
    first_b = next(e for e in solver.calls[0] if e['subject_code'] == 'B')
    assert first_b['lock']['room'] is False                          # TBA not protected on round 1
    # Successful row A was frozen at its round-1 result, not re-solved.
    last_a = next(e for e in solver.calls[-1] if e['subject_code'] == 'A')
    assert last_a['selected'] is False and all(last_a['lock'].values()) and last_a['room_id'] == 19
    a_final = next(g for g in body['schedule_data'] if g['subject_code'] == 'A')
    assert a_final['room_id'] == 19
    # Unselected row C: sent unchanged on every round.
    assert all(next(e for e in call if e['subject_code'] == 'C')['room_id'] == 18 for call in solver.calls)
    # Evaluation is computed from the final applied result.
    assert body['evaluation']['incomplete'] == []


@requires_db
def test_user_lock_is_never_released_and_named_in_the_failure(monkeypatch):
    solver = _ScriptedSolver(b_needs=('room', 'schedule'))
    rows = [_sel('B', (True, True, True), user=(False, True, False),
                 room_id=None, room='TBA', start_time=time(13, 0), end_time=time(16, 0))]
    body = _regen(monkeypatch, solver, rows)
    assert body['result_status'] == 'REGENERATION_INFEASIBLE' and 'schedule_data' not in body
    assert body['error'] == 'B could not be resolved while Time/Days is locked by you.'
    assert all(next(e for e in call if e['subject_code'] == 'B')['lock']['schedule'] for call in solver.calls)
    # The AUTO-kept Instructor was released on the way (then nothing legitimate was left).
    assert solver.calls[-1][0]['lock']['faculty'] is False


@requires_db
def test_failure_only_after_every_auto_kept_field_was_released(monkeypatch):
    solver = _ScriptedSolver(b_needs=('never',))                     # no feasible assignment exists
    rows = [_sel('B', (True, True, True), room_id=None, room='TBA')]
    body = _regen(monkeypatch, solver, rows)
    assert body['result_status'] == 'REGENERATION_INFEASIBLE'
    assert body['error'] == ('B could not be resolved because no valid faculty, room, and time '
                             'combination is currently available under the scheduling constraints.')
    released_order = []
    for prev, cur in zip(solver.calls, solver.calls[1:]):
        for f in FIELDS:
            if prev[0]['lock'][f] and not cur[0]['lock'][f]:
                released_order.append(f)
    assert released_order == ['schedule', 'faculty']     # Room (TBA) open from the start; Instructor last
    assert 'Try unlocking' not in body['error']


# ── Real data: EDUC 022/023 (BEED 4, AY2526) ────────────────────────────────

@pytest.fixture(scope='module')
def beed4():
    body = app_module.app.test_client().post('/api/schedule/retrieve-previous', json={
        'program': 'BEED', 'yearLevel': 4, 'term': 'A', 'acadYear': 'AY2627',
        'section': '12', 'curriculum': '2022-2023'}).get_json()
    assert body['success'] is True
    return body['schedule_data']


_PLUCK = ['subject_code', 'class_type', 'course', 'description', 'lec_hours', 'lab_hours', 'units',
          'faculty_id', 'instructor', 'room_id', 'room', 'room_type', 'days_list', 'day', 'time', 'days',
          'incomplete', 'incomplete_reason', 'incomplete_components']


def _live_regen(rows, locks, users=None):
    users = users or {}
    payload = [dict({k: r[k] for k in _PLUCK if k in r}, row_key=r['subject_code'],
                    selected=r['subject_code'] in locks,
                    lock=locks.get(r['subject_code'], {f: True for f in FIELDS}),
                    user_lock=users.get(r['subject_code'], {}))
               for r in rows]
    return app_module.app.test_client().post('/api/schedule/generate', json={
        'program': 'BEED', 'yearLevel': 4, 'term': 'A', 'acadYear': 'AY2627', 'section': '12',
        'curriculum': '2022-2023', 'locked_sessions': payload}).get_json()


@requires_db
def test_educ_retrieved_tba_rooms_are_incomplete(beed4):
    by = {r['subject_code']: r for r in beed4}
    for code in ('EDUC 021', 'EDUC 022', 'EDUC 023'):
        assert by[code]['room'] == 'TBA' and 'room' in by[code]['incomplete_components']


@requires_db
def test_educ_022_023_regenerate_without_manual_unlocking(beed4):
    # The exact locks that used to fail: conflict fields open, Room "kept" at TBA.
    open_fs = {'faculty': False, 'schedule': False, 'room': True}
    body = _live_regen(beed4, {'EDUC 022': open_fs, 'EDUC 023': open_fs})
    assert body['success'] is True, body.get('error')
    by = {g['subject_code']: g for g in body['schedule_data']}
    for code in ('EDUC 022', 'EDUC 023'):
        assert by[code]['room'] not in (None, '', 'TBA') and by[code]['room_id']
    # Unselected EDUC 021 is untouched (still TBA, still flagged).
    before = next(r for r in beed4 if r['subject_code'] == 'EDUC 021')
    after = by['EDUC 021']
    assert (after['instructor'], after['days'], after['room']) == (before['instructor'], before['days'], 'TBA')
    ev = body['evaluation']
    assert [(e['subject_code'], e['components']) for e in ev['incomplete']] == [('EDUC 021', ['room'])]
    assert not any(t['subject_code'] in ('EDUC 022', 'EDUC 023') for c in ev['conflicts'] for t in c['targets'])


@requires_db
def test_educ_023_room_only_releases_time_and_keeps_the_instructor(beed4):
    body = _live_regen(beed4, {'EDUC 023': {'faculty': True, 'schedule': True, 'room': False}})
    assert body['success'] is True, body.get('error')
    assert 'faculty' not in body['released_locks'].get('EDUC 023', [])
    g = next(x for x in body['schedule_data'] if x['subject_code'] == 'EDUC 023')
    assert g['instructor'] == 'Magtibay, Joel' and g['room'] not in (None, 'TBA')


@requires_db
def test_educ_022_user_locked_time_is_named_not_released(beed4):
    body = _live_regen(beed4, {'EDUC 022': {'faculty': False, 'schedule': True, 'room': False}},
                       {'EDUC 022': {'schedule': True}})
    assert body['result_status'] == 'REGENERATION_INFEASIBLE' and 'schedule_data' not in body
    assert body['error'] == 'EDUC 022 could not be resolved while Time/Days is locked by you.'


def test_tba_room_and_faculty_are_allowed_at_save_and_publish_but_still_flagged_for_generation():
    refs = {'faculty': {'F1'}, 'timeslot': set(), 'room': {'1'}, 'subjects': None}
    row = {'room_id': None, 'room': 'TBA', 'faculty_id': 'TBA', 'instructor': 'TBA'}
    comps, reasons = app_module._unresolved_components(row, refs)
    assert {'room', 'faculty'} <= set(comps)                  # Generation / Regeneration
    comps2, reasons2 = app_module._without_tba_components(row, comps, reasons)
    assert 'room' not in comps2 and 'faculty' not in comps2
    assert 'Room is TBA (not assigned).' not in reasons2 and 'Instructor is not assigned.' not in reasons2
    bad = {'room_id': '42', 'room': 'ZZ42', 'faculty_id': 'NOPE-1'}   # nonexistent records stay unresolved
    left = app_module._without_tba_components(bad, *app_module._unresolved_components(bad, refs))[0]
    assert {'room', 'faculty'} <= set(left)
