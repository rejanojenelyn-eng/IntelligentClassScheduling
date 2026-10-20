"""
Generate Schedule → Re-generate Selected: Time and Days lock separately.

The page sends lock.day / lock.time (lock.schedule = both). A kept Day pins the
meeting days while the solver picks a new time; a kept Time pins start/end while
the solver picks new days. Older payloads (lock.schedule only) keep both halves.
"""
from datetime import time

import app as app_module
import scheduler
from conftest import requires_db

CTX = {'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627'}


def _entry(flags, **kw):
    base = {'lock': flags, 'days_list': ['Wednesday', 'Friday'], 'day': 'Wednesday',
            'start_time': time(13, 30), 'end_time': time(15, 0)}
    base.update(kw)
    return base


# ── schedule_pin / fits_schedule_pin ────────────────────────────────────────

def test_pin_day_only_keeps_days_and_frees_time():
    days, tm = scheduler.schedule_pin(_entry({'day': True, 'time': False, 'schedule': False}))
    assert days == ['Wednesday', 'Friday'] and tm is None


def test_pin_time_only_keeps_time_and_frees_days():
    days, tm = scheduler.schedule_pin(_entry({'day': False, 'time': True, 'schedule': False}))
    assert days is None and tm == (time(13, 30), time(15, 0))


def test_whole_slot_lock_and_no_lock_pin_nothing():
    assert scheduler.schedule_pin(_entry({'schedule': True, 'day': True, 'time': True})) == (None, None)
    assert scheduler.schedule_pin(_entry({'schedule': False})) == (None, None)
    assert scheduler.schedule_pin(None) == (None, None)


def test_pin_flag_without_a_value_pins_nothing():
    assert scheduler.schedule_pin(_entry({'day': True}, days_list=[], day=None)) == (None, None)
    assert scheduler.schedule_pin(_entry({'time': True}, start_time=None)) == (None, None)


def test_fits_schedule_pin():
    pin = (['Wednesday', 'Friday'], None)
    assert scheduler.fits_schedule_pin(pin, ['Friday', 'Wednesday'], time(7, 30), time(9, 0))
    assert not scheduler.fits_schedule_pin(pin, ['Monday', 'Thursday'], time(13, 30), time(15, 0))
    pin = (None, (time(13, 30), time(15, 0)))
    assert scheduler.fits_schedule_pin(pin, ['Monday'], time(13, 30), time(15, 0))
    assert not scheduler.fits_schedule_pin(pin, ['Wednesday', 'Friday'], time(7, 30), time(9, 0))


def test_enforce_user_locks_restores_only_the_locked_half():
    sched = scheduler.IntelligentScheduler.__new__(scheduler.IntelligentScheduler)
    key = ('AGRI 301', 'Lecture', 'BSAM')
    lock = _entry({'faculty': False, 'room': False, 'schedule': False, 'day': True, 'time': False},
                  subject_code=key[0], class_type=key[1], course=key[2])
    gene = {'subject_code': key[0], 'class_type': key[1], 'course': key[2],
            'days_list': ['Monday', 'Thursday'], 'day': 'Monday', 'days': 'MON/THU',
            'start_time': time(7, 30), 'end_time': time(9, 0), 'time': '7:30 AM – 9:00 AM'}
    sched._enforce_user_locks([gene], {key: lock})
    assert gene['days_list'] == ['Wednesday', 'Friday'] and gene['days'] == 'WED/FRI'
    assert (gene['start_time'], gene['end_time']) == (time(7, 30), time(9, 0))   # time stays free


# ── API payload normalization / progressive release ─────────────────────────

def test_normalize_fills_halves_and_whole_slot_flag():
    ls = app_module._normalize_regen_locks({
        'lock': {'faculty': True, 'room': False, 'day': True, 'time': False},
        'user_lock': {'day': True}})
    assert ls['lock'] == {'faculty': True, 'room': False, 'day': True, 'time': False, 'schedule': False}
    assert ls['user_lock']['day'] is True and ls['user_lock']['schedule'] is False


def test_normalize_old_payload_locks_both_halves_with_schedule():
    ls = app_module._normalize_regen_locks({
        'lock': {'faculty': True, 'room': True, 'schedule': True},
        'user_lock': {'schedule': True}})
    assert ls['lock']['day'] is ls['lock']['time'] is ls['lock']['schedule'] is True
    assert ls['user_lock']['day'] is ls['user_lock']['time'] is ls['user_lock']['schedule'] is True


def test_release_keeps_a_user_locked_half():
    lock = {'schedule': True, 'day': True, 'time': True}
    app_module._release_schedule_lock(lock, {'day': True})
    assert lock == {'schedule': False, 'day': True, 'time': False}


# ── Real DB: Re-generate Selected honours each half ─────────────────────────

_PLUCK = ['subject_code', 'class_type', 'course', 'description', 'lec_hours', 'lab_hours', 'units',
          'faculty_id', 'instructor', 'room_id', 'room', 'room_type', 'days_list', 'day', 'time', 'days']
_ALL = {'faculty': True, 'room': True, 'day': True, 'time': True, 'schedule': True}


@requires_db
def test_regenerate_keeps_a_locked_day_and_a_locked_time():
    client = app_module.app.test_client()
    rows = client.post('/api/schedule/retrieve-previous', json=CTX).get_json()['schedule_data']
    codes = sorted({r['subject_code'] for r in rows})
    day_code, time_code = codes[0], codes[1]
    locks = {
        day_code:  {'faculty': False, 'room': False, 'day': True,  'time': False, 'schedule': False},
        time_code: {'faculty': False, 'room': False, 'day': False, 'time': True,  'schedule': False},
    }
    payload = [dict({k: r[k] for k in _PLUCK if k in r}, row_key=r['subject_code'],
                    selected=r['subject_code'] in locks, lock=locks.get(r['subject_code'], _ALL),
                    user_lock=locks.get(r['subject_code'], {}))
               for r in rows]
    body = client.post('/api/schedule/generate', json=dict(
        CTX, curriculum='2022-2023', section='', locked_sessions=payload)).get_json()
    assert body.get('schedule_data'), body.get('error')
    before = {(r['subject_code'], r.get('class_type')): r for r in rows}
    for g in body['schedule_data']:
        r = before.get((g['subject_code'], g.get('class_type')))
        if not r:
            continue
        if g['subject_code'] == day_code:
            assert sorted(g['days_list']) == sorted(r['days_list']), (g['days'], r['days'])
        elif g['subject_code'] == time_code:
            assert g['time'] == r['time'], (g['time'], r['time'])
        else:   # unselected rows come back exactly as sent
            assert (g['time'], g['days']) == (r['time'], r['days'])
