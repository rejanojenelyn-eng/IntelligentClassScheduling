"""Regression: a room double-booking published unnoticed (EDUC 018 BEED3 vs GEED 035
BEED-1, Saturday 6-9 PM, LQ117).

Two independent holes let it through the cross-schedule (HC15/HC11) check:
1. The editor sent the slice as day='' with a stale daydesc='Saturday'. Validators read
   day/days_list (and skip a day-less row) but _insert_batch saves daydesc.
2. The editor sends room/section ids as strings ('18'); DB rows carry ints (18), and
   overlapping_resource_conflicts compared them with ==.
"""
import datetime

from constraints import context_conflicts as cc


def test_string_and_int_ids_are_the_same_room_and_section():
    a = {'faculty_id': '19007', 'room_id': '18', 'section_id': '466'}
    b = {'faculty_id': '10011', 'room_id': 18, 'section_id': 9}
    assert cc.overlapping_resource_conflicts(a, b) == ['Room']
    assert cc.overlapping_resource_conflicts(
        {'faculty_id': None, 'room_id': None, 'section_id': '9'},
        {'faculty_id': None, 'room_id': None, 'section_id': 9}) == ['Section']


def test_tba_or_blank_rooms_never_conflict():
    for room in ('TBA', '', None, 'tba'):
        assert 'Room' not in cc.overlapping_resource_conflicts(
            {'room_id': room, 'section_id': 1}, {'room_id': room, 'section_id': 2})


def test_merge_still_exempts_room():
    assert cc.overlapping_resource_conflicts(
        {'room_id': '18', 'section_id': 1}, {'room_id': 18, 'section_id': 2}, valid_merge=True) == []


def test_rehydrate_uses_daydesc_when_day_is_blank():
    import app as A
    row = A._rehydrate_schedule([{'subject_code': 'EDUC 018', 'day': '', 'daydesc': 'Saturday',
                                  'start_time': '06:30 PM', 'end_time': '08:00 PM'}])[0]
    # Validators now see the same day _insert_batch will save.
    assert row['day'] == 'Saturday'
    assert row['days_list'] == ['Saturday']
    assert row['start_time'] == datetime.time(18, 30)


def test_rehydrate_day_wins_over_stale_daydesc():
    import app as A
    row = A._rehydrate_schedule([{'subject_code': 'EDUC 018', 'day': 'Wednesday', 'daydesc': 'Saturday',
                                  'start_time': '06:30 PM', 'end_time': '08:00 PM'}])[0]
    assert row['day'] == 'Wednesday'
    assert row['daydesc'] == 'Wednesday'   # _insert_batch reads daydesc — must match what was validated


def test_publish_counts_other_sections_drafts_but_generation_does_not():
    """Publish (include_drafts=True) must not take a room/time another section's saved
    Draft holds; the default (Generation / Save Draft) still counts Published only."""
    import inspect
    import app as A
    sig = inspect.signature(A._check_cross_schedule_conflicts)
    assert sig.parameters['include_drafts'].default is False
    src = inspect.getsource(A.api_approve_schedule)
    assert 'include_drafts=True' in src
