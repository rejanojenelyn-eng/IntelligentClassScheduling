"""Generate Schedule: plain-language notices when a class can't be completed because
rooms, instructors or time ran out — what is missing, why, and what to do — instead of
raw rule text ("HC13: ... disable the Laboratory Room Requirement in Settings")."""
from datetime import time

import pytest

import app as app_module

LABS = [{'roomid': 1, 'roomname': 'LAB1', 'roomtype': 'Laboratory'},
        {'roomid': 2, 'roomname': 'LAB2', 'roomtype': 'Laboratory'},
        {'roomid': 3, 'roomname': 'LQ101', 'roomtype': 'Lecture'}]
DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')


def _lab_row(**kw):
    row = {'subject_code': 'EETE 101', 'description': 'Basic Electrical Instruments', 'class_type': 'Lab',
           'incomplete': True, 'incomplete_components': ['room'], 'instructor': 'Argamosa, Arjay',
           'faculty_id': 'EMP007', 'days_list': ['Monday', 'Thursday'], 'day': 'Monday',
           'start_time': time(13, 30), 'end_time': time(16, 30), 'duration_hrs': 3.0, 'room_id': None,
           'incomplete_reason': ['HC13: "EETE 101" has laboratory hours but ...', 'Room is TBA (not assigned).']}
    row.update(kw)
    return row


@pytest.fixture
def stub(monkeypatch):
    state = {'room_slots': {}}
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: list(LABS))
    monkeypatch.setattr(app_module.scheduler_engine, 'fetch_published_room_faculty_slots',
                        lambda *a, **k: (state['room_slots'], {}))
    return state


def _notices(rows, **kw):
    return app_module._generation_shortage_notices(rows, 'A', 'AY2627', 97, **kw)


def test_all_lab_rooms_full_says_so_plainly(stub):
    whole_day = [(time(7, 30), time(21, 0))]
    stub['room_slots'] = {(rid, d): whole_day for rid in (1, 2) for d in DAYS}
    [n] = _notices([_lab_row()])
    assert n['kind'] == 'no_room' and n['title'] == 'All laboratory rooms are full'
    assert 'All 2 laboratory rooms are already booked' in n['message'] and '3-hour block' in n['message']
    assert 'TBA' in n['suggestion']
    assert 'HC13' not in n['message']


def test_free_labs_but_no_shared_time_is_explained(stub):
    [n] = _notices([_lab_row()])
    assert n['kind'] == 'no_room_at_time'
    assert '2 of 2 laboratory rooms still have free 3-hour blocks' in n['message']
    assert 'Argamosa, Arjay' in n['message']


def test_no_lab_rooms_configured(stub, monkeypatch):
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: [LABS[2]])
    [n] = _notices([_lab_row()])
    assert n['kind'] == 'no_room' and 'No laboratory rooms set up' == n['title']


def test_missing_instructor(stub):
    [n] = _notices([_lab_row(incomplete_components=['faculty'], class_type='Lecture', faculty_id=None,
                             instructor=None, incomplete_reason=[])])
    assert n['kind'] == 'no_faculty' and n['title'] == 'No instructor available'


def test_faculty_over_load_limit(stub):
    rows = [{'subject_code': 'TMHM 002', 'faculty_id': 'F1', 'instructor': 'Chan-Magtibay, Lesley Ann'}]
    viol = [{'rule': 'HC9', 'subject': 'multiple', 'faculty_id': 'F1',
             'detail': 'Chan-Magtibay, Lesley Ann Teaching Substitution load 14.0 hrs exceeds TS limit of 13.0 hrs.'}]
    [n] = _notices(rows, violations=viol)
    assert n['kind'] == 'faculty_load' and n['title'] == 'Chan-Magtibay, Lesley Ann is over their load limit'
    assert 'TMHM 002' in n['message'] and '14.0 hrs' in n['message']


def test_complete_schedule_has_no_notices(stub):
    assert _notices([_lab_row(incomplete=False, incomplete_components=[], room_id=1)]) == []


def test_sunday_only_class_counts_sunday_rooms_only(stub, monkeypatch):
    """NSTP meets on Sunday only: a room free Monday–Saturday doesn't help it."""
    lec = [{'roomid': 3, 'roomname': 'LQ101', 'roomtype': 'Lecture'}]
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: list(lec))
    stub['room_slots'] = {(3, 'Sunday'): [(time(7, 30), time(21, 0))]}
    [room, fac] = _notices([_lab_row(subject_code='NSTP 001', description='NSTP', class_type='Lecture',
                                     incomplete_components=['faculty', 'room'], faculty_id=None, instructor=None,
                                     days_list=['Sunday'], day='Sunday', start_time=time(9, 0), end_time=time(12, 0),
                                     incomplete_reason=[])])
    assert room['kind'] == 'no_room' and 'on Sunday' in room['message']
    assert fac['kind'] == 'no_faculty'
