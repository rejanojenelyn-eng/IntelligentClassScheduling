"""
Constraint-fix Phase 4: Local Scheduler constraint enforcement.

Local Schedule Adjustments now apply, through the SAME rule definitions CSP uses:
  HC1-HC4 faculty teaching windows, HC5 restricted day (NSTP/OU), HC6 standard
  time blocks, HC8 designee night-teaching day limit (effective schedule),
  HC13 laboratory room for the LAB PART only.
Intentional exclusions: HC7 day pairing, HC9 load (placement only, load-neutral).
Published Local is operational; Draft Local is not.
No real database is touched.
"""
import inspect
from datetime import time

import pytest

import app as app_module
import database

BASE_CFG = {'hc_faculty_load_enabled': 1, 'hc_weekend_enabled': 1, 'hc_weekend_day': 'sunday_only',
            'hc_weekend_subject': 'nstp_only', 'hc_time_blocks_enabled': 1,
            'hc_lab_session_enabled': 1}

T = {1: time(7, 30), 2: time(9, 0), 3: time(10, 30), 4: time(9, 15), 5: time(18, 0),
     6: time(19, 30), 7: time(21, 0), 8: time(16, 30), 9: time(13, 30), 10: time(15, 0),
     11: time(12, 0)}


class _Cur:
    """Routes the validators' SQL to canned rows."""

    def __init__(self, room='Lecture', subject=None, official_room=None, existing=()):
        self.room, self.official_room, self.existing = room, official_room, list(existing)
        self.subject = subject or {'laboratoryhours': 0, 'lecturehours': 3}
        self.sql, self.params, self.calls = '', None, []

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        self.calls.append(sql)

    def fetchall(self):
        if 'FROM public.timeslot' in self.sql:
            ids = self.params[0] if isinstance(self.params[0], list) else list(self.params)
            return [{'timeid': i, 'timevalue': T[i]} for i in ids if i in T]
        if 'UNION ALL' in self.sql:
            return [dict(r) for r in self.existing]
        return []

    def fetchone(self):
        if 'SELECT roomtype FROM public.room' in self.sql:
            return {'roomtype': self.room}
        if 'FROM public.curriculumsubject' in self.sql:
            return dict(self.subject)
        if 'LEFT JOIN public.room r ON ss.roomid = r.roomid' in self.sql:
            return {'roomtype': self.official_room} if self.official_room else None
        if 'FROM public.faculty f' in self.sql:
            return {'employeetypeid': 1, 'designationid': None}
        return None


@pytest.fixture
def cfg(monkeypatch):
    current = dict(BASE_CFG)
    monkeypatch.setattr(database, 'load_scheduler_config', lambda: current)
    return current


def _edit(cur, code='IT101', day='Monday', start=2, end=3, official=None):
    sess = {'subjectcode': code, 'daydesc': day, 'starttimeid': start, 'endtimeid': end,
            'roomid': 1, 'official_sessionid': official}
    return app_module._validate_local_editable_dimensions(cur, sess, 'F1')


# ── HC5 restricted day: same NSTP/OU rule as CSP ────────────────────────────

def test_hc5_ou_subject_allowed_on_sunday_like_csp(cfg):
    ok, err = _edit(_Cur(), code='OU101', day='Sunday')
    assert ok, err            # old Local rule allowed only NSTP under 'nstp_only'


def test_hc5_non_nstp_subject_rejected_on_sunday(cfg):
    ok, err = _edit(_Cur(), code='IT101', day='SUN')
    assert not ok and 'Sunday' in err


def test_hc5_all_allowed_and_disabled(cfg):
    cfg['hc_weekend_subject'] = 'all_allowed'
    assert _edit(_Cur(), code='IT101', day='Sunday')[0]
    cfg.update(hc_weekend_subject='nstp_only', hc_weekend_enabled=0)
    assert _edit(_Cur(), code='IT101', day='Sunday')[0]


# ── HC6 standard time blocks ────────────────────────────────────────────────

def test_hc6_off_grid_time_rejected_and_standard_block_allowed(cfg):
    ok, err = _edit(_Cur(), start=4, end=3)          # 9:15-10:30
    assert not ok and 'invalid start time' in err
    assert _edit(_Cur(), start=2, end=3)[0]          # 9:00-10:30
    cfg['hc_time_blocks_enabled'] = 0
    assert _edit(_Cur(), start=4, end=3)[0]


# ── HC13 laboratory room for the lab part only ──────────────────────────────

LEC_LAB = {'laboratoryhours': 3, 'lecturehours': 2}
LAB_ONLY = {'laboratoryhours': 3, 'lecturehours': 0}


def test_hc13_lecture_part_of_lec_lab_subject_may_use_lecture_room(cfg):
    ok, err = _edit(_Cur(room='Lecture', subject=LEC_LAB, official_room='Lecture'), official=55)
    assert ok, err            # old Local rule forced every session of a lab subject into a lab


def test_hc13_lab_part_moved_to_lecture_room_is_rejected(cfg):
    ok, err = _edit(_Cur(room='Lecture', subject=LEC_LAB, official_room='Laboratory'), official=55)
    assert not ok and 'laboratory room' in err


def test_hc13_lab_only_subject_needs_lab_room_and_lab_room_always_passes(cfg):
    assert not _edit(_Cur(room='Lecture', subject=LAB_ONLY))[0]
    assert _edit(_Cur(room='Laboratory', subject=LAB_ONLY))[0]
    assert _edit(_Cur(room='Laboratory', subject=LEC_LAB, official_room='Laboratory'), official=55)[0]
    cfg['hc_lab_session_enabled'] = 0
    assert _edit(_Cur(room='Lecture', subject=LAB_ONLY))[0]


# ── HC1-HC4 / HC8 faculty service rules (transaction level) ────────────────

def _fac(status='Permanent', designation=None, nights=None, regular_start=time(7, 30),
         restrict=True, ts=0, regularload=18):
    return {'fullname': 'Faculty One', 'employeestatus': status, 'designationid': designation,
            'nightteachingservice': nights,
            'employeetype': {'regular_start': regular_start, 'regular_end': time(16, 30),
                             'parttime_start': time(16, 30), 'parttime_end': time(21, 0),
                             'restrict_pt_hours': restrict, 'teachingsubstitution': ts,
                             'regularload': regularload, 'parttimeload': 12}}


def _service(monkeypatch, fac_row, sessions, existing=()):
    monkeypatch.setattr(app_module, '_load_faculty_map', lambda *a, **k: {'F1': fac_row})
    cur = _Cur(existing=existing)
    return app_module._validate_local_faculty_service_rules(cur, sessions, 1), cur


def _s(code='IT101', day='Monday', start=2, end=3, official=100):
    return {'subjectcode': code, 'daydesc': day, 'starttimeid': start, 'endtimeid': end,
            'faculty': 'F1', 'official_sessionid': official}


def test_hc1_full_time_class_before_regular_window_rejected(monkeypatch, cfg):
    (ok, err), _ = _service(monkeypatch, _fac(regular_start=time(8, 0)), [_s(start=1, end=3)])
    assert not ok and 'IT101' in err                         # 7:30-10:30, window starts 8:00
    (ok, err), _ = _service(monkeypatch, _fac(regular_start=time(8, 0)), [_s(start=1, end=2)])
    assert ok, err                                           # 7:30-9:00 AM PT/TS slot


def test_hc3_part_time_faculty_daytime_move_rejected_evening_allowed(monkeypatch, cfg):
    pt = _fac(status='Part-Time')
    (ok, err), _ = _service(monkeypatch, pt, [_s(start=2, end=3)])
    assert not ok and 'Part-time faculty' in err
    (ok, err), _ = _service(monkeypatch, pt, [_s(start=5, end=6)])
    assert ok, err


def test_designee_class_crossing_the_morning_window_is_split_not_rejected(monkeypatch, cfg):
    # Designee segment policy (same rule as the Official validator): 7:30-10:30 is
    # 7:30-9:00 (morning, Regular/TS) + 9:00-10:30 (Regular/TS) — valid, never rejected
    # as a whole by the old 8:00-17:00 designee window.
    desig = _fac(designation=3, nights=0)
    (ok, err), _ = _service(monkeypatch, desig, [_s(start=1, end=3)])   # 7:30-10:30
    assert ok, err
    (ok, err), _ = _service(monkeypatch, desig, [_s(start=9, end=8)])   # 13:30-16:30
    assert ok, err


def test_designee_class_crossing_into_the_pt_window_is_split_not_rejected(monkeypatch, cfg):
    # 15:00-18:00 = 15:00-16:30 Regular/TS + 16:30-18:00 PT/TS — every segment is valid.
    (ok, err), _ = _service(monkeypatch, _fac(designation=3, nights=2), [_s(start=10, end=5)])
    assert ok, err


def _night(day, start=5, end=6, code='IT201'):
    return {'daydesc': day, 'start_time': T[start], 'end_time': T[end], 'subject_code': code}


def test_hc8_move_adding_a_night_over_the_allowance_is_rejected(monkeypatch, cfg):
    desig = _fac(designation=3, nights=1)
    (ok, err), _ = _service(monkeypatch, desig, [_s(day='Tuesday', start=5, end=6)],
                            existing=[_night('Monday')])
    assert not ok and 'allowed up to 1' in err


def test_hc8_move_within_allowance_and_same_night_is_allowed(monkeypatch, cfg):
    desig = _fac(designation=3, nights=1)
    # The replaced occurrence is not in the effective context -> moving a night class to another night.
    (ok, err), _ = _service(monkeypatch, desig, [_s(day='Tuesday', start=5, end=6)])
    assert ok, err
    # Pre-existing excess (Mon + Wed with allowance 1) does not block a move onto an already-used night.
    (ok, err), _ = _service(monkeypatch, desig, [_s(day='Monday', start=6, end=7)],
                            existing=[_night('Monday'), _night('Wednesday')])
    assert ok, err


def test_service_rules_follow_the_faculty_load_toggle(monkeypatch, cfg):
    cfg['hc_faculty_load_enabled'] = 0
    (ok, _), _ = _service(monkeypatch, _fac(status='Part-Time'), [_s(start=2, end=3)])
    assert ok


def test_effective_context_excludes_replaced_occurrences_and_draft_local(monkeypatch, cfg):
    (_ok, _), cur = _service(monkeypatch, _fac(designation=3, nights=1), [_s(official=100)])
    sql = next(s for s in cur.calls if 'UNION ALL' in s)
    assert "la.status = 'Published' AND la.is_active = TRUE" in sql      # Draft Local not operational
    assert 'NOT (ss.sessionid = ANY(%s))' in sql                          # proposal replaces its Official rows
    assert 'las_x.official_sessionid = ss.sessionid' in sql               # superseded Official excluded


# ── intentional exclusions ──────────────────────────────────────────────────

def test_hc9_load_is_not_applied_to_local_moves(monkeypatch, cfg):
    (ok, err), _ = _service(monkeypatch, _fac(regularload=0), [_s(start=2, end=3)])
    assert ok, err


def test_hc7_day_pairing_is_not_applied_to_local_moves():
    for fn in (app_module._validate_local_editable_dimensions,
               app_module._validate_local_faculty_service_rules):
        src = inspect.getsource(fn)
        assert '_check_day_pairing' not in src and '_check_load_limits' not in src


# ── wiring: save, conflict check and publish all enforce the service rules ──

def test_all_three_local_paths_run_the_service_rules():
    for fn in (app_module.api_save_local_arrangement, app_module.api_local_check_room_conflicts,
               app_module.api_publish_local_arrangement):
        body = inspect.getsource(fn)
        assert '_validate_local_faculty_service_rules(' in body, fn.__name__
        assert 'INVALID_LOCAL_EDITABLE_DIMENSIONS' in body


def test_publish_rejects_when_service_rules_fail(monkeypatch):
    from test_phase3b_local_publish_consistency import _publish, NSTP_ONLY
    monkeypatch.setattr(app_module, '_validate_local_faculty_service_rules',
                        lambda *a, **k: (False, 'IT101: outside window'))
    status, body = _publish(monkeypatch, cand_code='IT101', cand_fac='F1', other_code='IT102',
                            other_fac='F2', cfg=NSTP_ONLY)
    assert status == 409 and body['code'] == 'INVALID_LOCAL_EDITABLE_DIMENSIONS'
    assert body['error'] == 'IT101: outside window'
