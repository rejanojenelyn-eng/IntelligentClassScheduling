"""The cross-section repair must release a HISTORICAL (CBR) room/time that collides with
another section's booking, but never touch a room/time the USER locked.

Before: a CBR-protected room was treated like a user lock ("Skipping ...: locked by user
selection"), so the clash could not be repaired and the class came out "Room is TBA".
"""
from datetime import time

import app


def _gene():
    return {'subject_code': 'ELEN 011', 'class_type': 'Lecture', 'course': 'DCVET',
            'faculty_id': 'F1', 'instructor': 'X', 'room_id': 36, 'room': '209B', 'room_type': 'Lecture',
            'day': 'Thursday', 'days_list': ['Thursday'], 'days': 'THU',
            'start_time': time(13, 30), 'end_time': time(16, 30), 'lec_hours': 3, 'lab_hours': 0}


ROOMS = [{'roomid': 36, 'roomname': '209B', 'roomtype': 'Lecture', 'capacity': 40},
         {'roomid': 37, 'roomname': '210', 'roomtype': 'Lecture', 'capacity': 40}]
PUB_ROOMS = {(36, 'Thursday'): [(time(13, 0), time(14, 30))]}       # another section holds 209B
KEY = ('ELEN 011', 'Lecture', 'DCVET')


def _repair(locked_parts):
    g = _gene()
    out = app.scheduler_engine._repair_cross_conflicts(
        [g], ROOMS, PUB_ROOMS, {}, faculty_map={'F1': {}}, locked_parts=locked_parts)
    return out[0]


def test_historical_room_is_released_and_repaired():
    lp = {KEY: {'lock': {'faculty': True, 'room': True, 'schedule': False},
                'source_case': 'DCVET-Y2-A-AY2526', 'room_id': 36}}
    g = _repair(lp)
    assert g['room_id'] != 36 or g['start_time'] != time(13, 30), 'clash was not repaired'
    assert lp[KEY]['lock']['room'] is False and lp[KEY]['release_reason']   # released for re-apply too


def test_user_locked_room_is_never_changed():
    lp = {KEY: {'lock': {'faculty': True, 'room': True, 'schedule': True}, 'room_id': 36}}   # no source_case = user
    g = _repair(lp)
    assert g['room_id'] == 36 and g['start_time'] == time(13, 30)
    assert lp[KEY]['lock']['room'] is True
