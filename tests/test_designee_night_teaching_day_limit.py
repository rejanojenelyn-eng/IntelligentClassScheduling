"""
Final HC8 — DESIGNEE_NIGHT_TEACHING_DAY_LIMIT regression tests.

Settings → Designee Scheduling Policy → "PT/Night Teaching Service" is the
MAXIMUM NUMBER OF DISTINCT DAYS per week a designee may teach inside the
6:00-9:00 PM Night Teaching Service window (0 = not allowed). Several slices on
the same day are one night; a TUE/FRI subject uses two; 4:30-6:00 PM never
counts.

Covers the validator (CSPValidator via ConstraintService), the GA builder and
mutation, selective-regeneration locks, the cross-section Save/Approve check,
and Retrieve Previous re-validation. Pure unit tests — no database needed.
"""
import random
from datetime import time

import pytest

import scheduler
from scheduler import CSPValidator, IntelligentScheduler
from constraints import ConstraintService
from constraints.policy import SchedulingPolicy

NO_GRID = {'hc_time_blocks_enabled': 0, 'hc_day_pairing_enabled': 0}


def _designee(emp='F1', allowance=0, name='Dean Cruz'):
    return {
        'employeenumber': emp, 'fullname': name, 'specializationname': '',
        'employeestatus': 'Permanent', 'designationid': 1,
        'nightteachingservice': allowance,
        'employeetype': {'regular_start': time(7, 30), 'regular_end': time(16, 30),
                         'regularload': 40, 'parttimeload': 40, 'teachingsubstitution': 0},
    }


def _regular(emp='F2'):
    f = _designee(emp, name='Regular Faculty')
    f['designationid'] = None
    return f


def _gene(day='Tuesday', start=(18, 0), end=(21, 0), code='IT101', fac='F1', room=1, days=None):
    return {
        'subject_code': code, 'class_type': 'Lecture', 'course': 'BSIT',
        'faculty_id': fac, 'room_id': room, 'room_type': 'Lecture',
        'day': (days or [day])[0], 'days_list': list(days or [day]),
        'start_time': time(*start), 'end_time': time(*end),
        'duration_hrs': 1.5,
    }


def _validate(schedule, faculty_map, cfg=NO_GRID):
    return ConstraintService(SchedulingPolicy(cfg)).validate_schedule(schedule, faculty_map).violations


def _hc8(violations):
    return [v for v in violations if v['rule'] == 'HC8']


def _rules(violations):
    return {v['rule'] for v in violations}


# ── 1. allowance 0 → any 6-9 PM teaching is rejected ────────────────────────

def test_1_allowance_zero_tuesday_night_is_invalid():
    fmap = {'F1': _designee(allowance=0, name='Academic Head')}
    v = _validate([_gene('Tuesday')], fmap)
    hc8 = _hc8(v)
    assert len(hc8) == 1
    assert hc8[0]['allowed_nights'] == 0
    assert hc8[0]['night_days'] == ['Tuesday']
    assert "does not allow PT/Night Teaching Service" in hc8[0]['detail']
    # One precise reason — not also a misleading "outside these windows" HC4.
    assert 'HC4' not in _rules(v)


# ── 2. allowance 1 → one night is valid ─────────────────────────────────────

def test_2_allowance_one_tuesday_night_is_valid():
    fmap = {'F1': _designee(allowance=1)}
    v = _validate([_gene('Tuesday')], fmap)
    assert 'HC8' not in _rules(v)
    assert 'HC4' not in _rules(v)


# ── 3. several slices the same evening = 1 night ────────────────────────────

def test_3_allowance_one_two_slices_same_tuesday_is_one_night():
    fmap = {'F1': _designee(allowance=1)}
    a = _gene('Tuesday', (18, 0), (19, 30), code='IT101', room=1)
    b = _gene('Tuesday', (19, 30), (21, 0), code='IT102', room=2)
    v = _validate([a, b], fmap)
    assert v == []   # no HC8, and no faculty overlap either


def test_3b_three_slices_three_subjects_same_night_count_once():
    nights = scheduler.collect_designee_night_days([
        _gene('Tuesday', (18, 0), (19, 0), code='A'),
        _gene('Tuesday', (19, 0), (20, 0), code='B'),
        _gene('Tuesday', (20, 0), (21, 0), code='C'),
    ], {'F1': _designee(allowance=1)})
    assert set(nights['F1']) == {'Tuesday'}


# ── 4. allowance 1, TUE + THU → 2 nights → invalid ──────────────────────────

def test_4_allowance_one_tuesday_and_thursday_is_invalid():
    fmap = {'F1': _designee(allowance=1)}
    v = _validate([_gene('Tuesday'), _gene('Thursday', code='IT102')], fmap)
    hc8 = _hc8(v)
    assert len(hc8) == 1 and hc8[0]['total_nights'] == 2


# ── 5/6. allowance 2 ────────────────────────────────────────────────────────

def test_5_allowance_two_monday_wednesday_is_valid():
    fmap = {'F1': _designee(allowance=2)}
    v = _validate([_gene('Monday'), _gene('Wednesday', code='IT102')], fmap)
    assert 'HC8' not in _rules(v)


def test_6_allowance_two_mon_wed_fri_is_invalid_with_spec_message():
    fmap = {'F1': _designee(allowance=2)}
    v = _validate([_gene('Friday', code='C'), _gene('Monday', code='A'),
                   _gene('Wednesday', code='B')], fmap)
    hc8 = _hc8(v)
    assert len(hc8) == 1
    assert hc8[0]['detail'] == (
        'Dean Cruz is allowed up to 2 PT/night teaching days per week for this '
        'designation, but the current schedule uses 3 night teaching days '
        '(Monday, Wednesday, Friday).')
    assert hc8[0]['faculty_id'] == 'F1'
    assert set(hc8[0]['subject'].split('/')) == {'A', 'B', 'C'}


# ── 7. one TUE/FRI subject consumes 2 nights ────────────────────────────────

def test_7_allowance_two_single_subject_tue_fri_uses_two_nights_and_is_valid():
    fmap = {'F1': _designee(allowance=2)}
    tf = _gene(days=['Tuesday', 'Friday'], start=(18, 0), end=(19, 30))
    assert set(scheduler.collect_designee_night_days([tf], fmap)['F1']) == {'Tuesday', 'Friday'}
    assert 'HC8' not in _rules(_validate([tf], fmap))


def test_7b_tue_fri_subject_exceeds_allowance_one():
    fmap = {'F1': _designee(allowance=1)}
    tf = _gene(days=['Tuesday', 'Friday'], start=(18, 0), end=(19, 30))
    assert _hc8(_validate([tf], fmap))[0]['total_nights'] == 2


# ── 8. 4:30-6:00 PM never consumes a night ──────────────────────────────────

def test_8_four_thirty_to_six_does_not_consume_night_allowance():
    fmap = {'F1': _designee(allowance=1)}
    pm = [_gene(d, (16, 30), (18, 0), code=f'PM{i}') for i, d in
          enumerate(['Monday', 'Wednesday', 'Thursday'])]
    night = _gene('Tuesday', code='NIGHT')
    v = _validate(pm + [night], fmap)
    assert 'HC8' not in _rules(v)      # only Tuesday is a night
    assert 'HC4' not in _rules(v)
    assert not scheduler.is_night_service_slice(time(16, 30), time(18, 0))
    assert scheduler.is_night_service_slice(time(16, 30), time(19, 30))


def test_8b_allowance_zero_still_allows_four_thirty_to_six():
    v = _validate([_gene('Tuesday', (16, 30), (18, 0))], {'F1': _designee(allowance=0)})
    assert v == []


# ── HC4 window fix: no "outside these windows" when an allowance remains ────

def test_hc4_recognizes_night_window_when_allowance_remains():
    fmap = {'F1': _designee(allowance=2)}
    v = _validate([_gene('Monday'), _gene('Tuesday', code='IT102')], fmap)
    assert not any('outside these windows' in x['detail'] for x in v)
    assert v == []


def test_hc4_still_flags_slices_past_nine_pm():
    v = _validate([_gene('Tuesday', (19, 30), (22, 0))], {'F1': _designee(allowance=2)})
    assert 'HC4' in _rules(v)


def test_night_days_are_weekday_nights_like_the_previous_hc8_scope():
    # Weekend designee teaching stays governed by the restricted-day rule
    # (HC4's designee windows are weekday-only too). A Mon-Sat change would
    # be the NIGHT_SERVICE_DAYS constant alone.
    fmap = {'F1': _designee(allowance=1)}
    v = _validate([_gene('Tuesday'), _gene('Saturday', code='SAT')], fmap)
    assert 'HC8' not in _rules(v)


def test_non_designees_are_unaffected():
    v = _validate([_gene(d, fac='F2', code=f'X{i}') for i, d in
                   enumerate(['Monday', 'Tuesday', 'Wednesday', 'Thursday'])],
                  {'F2': _regular()})
    assert 'HC8' not in _rules(v)


# ── 9. Retrieve Previous: historical rows are re-validated, not altered ─────

def test_9_retrieve_previous_rows_with_too_many_nights_are_flagged_unchanged():
    import app as app_module
    # Shape of retrieved historical rows (string times, one row per day).
    historical = [
        {'subject_code': s, 'faculty_id': 'F1', 'room_id': 1, 'room_type': 'Lecture',
         'day': d, 'days_list': [d], 'start_time': '18:00', 'end_time': '21:00',
         'duration_hrs': 3.0}
        for s, d in (('A', 'Monday'), ('B', 'Wednesday'), ('C', 'Friday'))
    ]
    snapshot = [dict(r) for r in historical]
    hydrated = app_module._rehydrate_schedule([dict(r) for r in historical])
    v = _validate(hydrated, {'F1': _designee(allowance=2)})
    hc8 = _hc8(v)
    assert len(hc8) == 1 and hc8[0]['night_days'] == ['Monday', 'Wednesday', 'Friday']
    assert historical == snapshot   # validation never rewrites the historical data


# ── Cross-section Save/Approve check uses the same rule ─────────────────────

class _FakeCursor:
    def __init__(self, rows): self._rows = rows
    def execute(self, *a, **k): pass
    def fetchall(self): return self._rows
    def close(self): pass


class _FakeConn:
    def __init__(self, rows): self._rows = rows
    def cursor(self, *a, **k): return _FakeCursor(self._rows)
    def close(self): pass


def _cross_check(monkeypatch, submitted, existing_rows, allowance):
    import app as app_module
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _FakeConn(existing_rows))
    return app_module._check_designee_night_limit(
        submitted, {'F1': _designee(allowance=allowance)}, sem_id=1,
        exclude_program='BSIT', exclude_year_level=1)


def test_cross_section_existing_monday_plus_new_tuesday_is_valid(monkeypatch):
    existing = [{'faculty_id': 'F1', 'daydesc': 'Monday', 'start_time': '18:00', 'end_time': '21:00'}]
    new = [{'faculty_id': 'F1', 'day': 'Tuesday', 'start_time': '06:00 PM', 'end_time': '09:00 PM'}]
    assert _cross_check(monkeypatch, new, existing, allowance=2) == []


def test_cross_section_existing_mon_tue_plus_new_wednesday_is_invalid(monkeypatch):
    existing = [
        {'faculty_id': 'F1', 'daydesc': 'Monday',  'start_time': '18:00', 'end_time': '21:00'},
        {'faculty_id': 'F1', 'daydesc': 'Tuesday', 'start_time': '18:00', 'end_time': '19:30'},
        {'faculty_id': 'F1', 'daydesc': 'Tuesday', 'start_time': '19:30', 'end_time': '21:00'},
        {'faculty_id': 'F1', 'daydesc': 'Thursday', 'start_time': '16:30', 'end_time': '18:00'},
    ]
    new = [{'faculty_id': 'F1', 'day': 'Wednesday', 'start_time': '06:00 PM', 'end_time': '09:00 PM'}]
    v = _cross_check(monkeypatch, new, existing, allowance=2)
    assert len(v) == 1
    assert v[0]['rule'] == 'HC8'
    assert v[0]['night_days'] == ['Monday', 'Tuesday', 'Wednesday']   # Thursday 4:30-6:00 not a night


# ── Solver: builder never exceeds the allowance ─────────────────────────────

def _sched():
    s = IntelligentScheduler()
    cfg = dict(NO_GRID)
    s.csp = CSPValidator(config=cfg)
    s._hc_cfg = cfg
    return s


def _subject(code, lec=1.5):
    return {'subjectcode': code, 'subjectname': code, 'lecturehours': lec,
            'laboratoryhours': 0, 'creditunits': 3, 'offeringcode': 'BSIT'}


def _busy_daytime(fid, days=('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')):
    busy = {(fid, d): [(time(7, 0), time(18, 0))] for d in days}
    busy[(fid, 'Saturday')] = [(time(7, 0), time(21, 0))]   # weekend is out of scope here
    return busy


def test_solver_only_uses_already_counted_nights_when_allowance_is_exhausted():
    # Allowance 2; MON + WED nights already used elsewhere (6:00-7:30 PM).
    # Daytime is full, so the only valid placements are MON/WED 7:30-9:00 PM —
    # a second class on an existing night, never a third night.
    fmap = {'F1': _designee(allowance=2)}
    pub = _busy_daytime('F1')
    pub[('F1', 'Monday')].append((time(18, 0), time(19, 30)))
    pub[('F1', 'Wednesday')].append((time(18, 0), time(19, 30)))
    sched = _sched()
    for seed in range(15):
        random.seed(seed)
        ind = sched._build_individual([_subject('IT101')], list(fmap.values()), fmap,
                                      [{'roomid': 1, 'roomname': 'R1', 'roomtype': 'Lecture'}],
                                      published_faculty_slots=pub)
        g = ind[0]
        assert g['faculty_id'] == 'F1'
        assert set(g['days_list']) <= {'Monday', 'Wednesday'}, (seed, g['days_list'])
        assert (g['start_time'], g['end_time']) == (time(19, 30), time(21, 0))


def test_solver_offers_night_blocks_to_designees_with_allowance():
    blocks = _sched()._get_allowed_blocks_for_faculty(
        _designee(allowance=1), [(time(18, 0), time(21, 0)), (time(16, 30), time(18, 0))])
    assert (time(18, 0), time(21, 0), 'pt') in blocks
    none = _sched()._get_allowed_blocks_for_faculty(
        _designee(allowance=0), [(time(18, 0), time(21, 0)), (time(8, 0), time(9, 30))])
    assert all(e <= time(16, 30) for (_s, e, _k) in none)


def test_mutation_cannot_add_a_night_beyond_the_allowance():
    fmap = {'F1': _designee(allowance=1)}
    sched = _sched()
    subjects = {'IT101': _subject('IT101'), 'IT102': _subject('IT102')}
    rooms = [{'roomid': 1, 'roomname': 'R1', 'roomtype': 'Lecture'},
             {'roomid': 2, 'roomname': 'R2', 'roomtype': 'Lecture'}]
    for seed in range(60):
        random.seed(seed)
        child = [_gene('Monday', code='IT101', room=1),
                 _gene('Tuesday', (8, 0), (9, 30), code='IT102', room=2)]
        for g in child:
            g['lec_hours'], g['lab_hours'] = 1.5, 0
        child = sched._mutate(child, subjects, list(fmap.values()), fmap, rooms)
        nights = scheduler.collect_designee_night_days(child, fmap).get('F1', {})
        assert len(nights) <= 1, (seed, [(g['days_list'], g['start_time']) for g in child])


# ── 10. Selective regeneration respects Instructor / Time locks ─────────────

_ROOMS = [{'roomid': 1, 'roomname': 'R1', 'roomtype': 'Lecture'}]


def test_10a_instructor_locked_time_free_moves_time_not_faculty():
    # F1 (allowance 1) already teaches Monday night elsewhere. Instructor is
    # locked, so the fix must come from Time/Days: never a new night day.
    fmap = {'F1': _designee(allowance=1), 'F2': _regular()}
    pub = {('F1', 'Monday'): [(time(18, 0), time(21, 0))]}
    locks = {('IT101', 'Lecture', 'BSIT'): {
        'lock': {'faculty': True}, 'faculty_id': 'F1',
        'day': 'Friday', 'days_list': ['Friday'],
        'start_time': time(18, 0), 'end_time': time(19, 30)}}
    sched = _sched()
    for seed in range(20):
        random.seed(seed)
        g = sched._build_individual([_subject('IT101')], list(fmap.values()), fmap, _ROOMS,
                                    published_faculty_slots=pub, locked_parts=locks)[0]
        assert g['faculty_id'] == 'F1'
        if scheduler.is_night_service_slice(g['start_time'], g['end_time']):
            assert g['days_list'] == ['Monday'], seed


def test_10b_time_locked_instructor_free_picks_faculty_with_allowance():
    fmap = {'F1': _designee('F1', allowance=1), 'F3': _designee('F3', allowance=2, name='Other')}
    pub = {('F1', 'Monday'): [(time(18, 0), time(21, 0))]}
    locks = {('IT101', 'Lecture', 'BSIT'): {
        'lock': {'schedule': True}, 'faculty_id': 'F1',
        'day': 'Wednesday', 'days_list': ['Wednesday'],
        'start_time': time(18, 0), 'end_time': time(19, 30)}}
    sched = _sched()
    for seed in range(20):
        random.seed(seed)
        g = sched._build_individual([_subject('IT101')], list(fmap.values()), fmap, _ROOMS,
                                    published_faculty_slots=pub, locked_parts=locks)[0]
        assert (g['days_list'], g['start_time']) == (['Wednesday'], time(18, 0))   # lock kept
        assert g['faculty_id'] == 'F3', seed


def test_10c_both_locked_keeps_assignment_and_reports_violation():
    fmap = {'F1': _designee(allowance=1)}
    pub = {('F1', 'Monday'): [(time(18, 0), time(21, 0))]}
    locks = {('IT101', 'Lecture', 'BSIT'): {
        'lock': {'faculty': True, 'schedule': True, 'room': True},
        'faculty_id': 'F1', 'room_id': 1,
        'day': 'Wednesday', 'days_list': ['Wednesday'],
        'start_time': time(18, 0), 'end_time': time(19, 30)}}
    random.seed(0)
    g = _sched()._build_individual([_subject('IT101')], list(fmap.values()), fmap, _ROOMS,
                                   published_faculty_slots=pub, locked_parts=locks)[0]
    assert (g['faculty_id'], g['days_list'], g['start_time']) == ('F1', ['Wednesday'], time(18, 0))
    monday = _gene('Monday', code='OTHER')
    assert _hc8(_validate([monday, g], fmap))   # reported, lock not silently broken
