"""
Phase A2 characterization: Requests (api_requests_validate) and Local
Arrangements (api_local_check_room_conflicts) conflict-checking semantics,
compared against the authoritative CSPValidator.

CONCLUSION (see the Phase A2 report for the full writeup): these two are NOT
migrated to ConstraintService this phase because they are not behaviorally
equivalent to it -- migrating them naively would be a silent behavior
change, which the task instructions explicitly forbid. Two concrete,
demonstrable divergences:

1. Shared math, different scope. Both raw-SQL checks use the same interval-
   overlap predicate CSPValidator._times_overlap already encodes
   (`start1 < end2 AND end1 > start2`) -- proven equal below. But they run
   it over a DIFFERENT universe: a specific calendar date (Requests'
   'makeup' type also checks other 'Approved' class_meeting_request rows
   for that exact date) or Local Arrangement/displacement bookkeeping
   (local_arrangement_sessions, local_displaced_subjects) that CSPValidator
   has no notion of at all -- it only ever validates a recurring
   day-of-week pattern across a supplied schedule list.

2. Missing exceptions. CSPValidator's HC9/HC10 checks have built-in
   exceptions (the NSTP/OU class-merge policy, multiple NSTP sections
   taught by the same faculty at the same time) that the raw SQL in
   api_requests_validate/api_local_check_room_conflicts does not implement
   at all. Proven below: CSPValidator explicitly ALLOWS a scenario the raw
   SQL pattern would flag as a hard conflict. Migrating either endpoint to
   ConstraintService as-is would silently start allowing/rejecting requests
   it doesn't today.
"""
from datetime import time

from scheduler import CSPValidator


def _overlap_via_sql_style(s1, e1, s2, e2):
    """Mirrors the exact predicate used throughout api_requests_validate and
    api_local_check_room_conflicts: `existing.start < new.end AND
    existing.end > new.start` (their SQL is `ts_s.timevalue < %s AND
    ts_e.timevalue > %s` with params [end_t, start_t])."""
    return s1 < e2 and e1 > s2


def test_requests_and_local_overlap_predicate_matches_cspvalidator_exactly():
    cases = [
        (time(9, 0), time(10, 30), time(9, 0), time(10, 30), True),   # identical
        (time(9, 0), time(10, 30), time(10, 30), time(12, 0), False),  # back-to-back, not overlapping
        (time(9, 0), time(10, 30), time(10, 0), time(11, 0), True),   # partial overlap
        (time(9, 0), time(10, 30), time(7, 0), time(9, 0), False),    # adjacent before
    ]
    for s1, e1, s2, e2, expected in cases:
        assert CSPValidator._times_overlap(s1, e1, s2, e2) == expected
        assert _overlap_via_sql_style(s1, e1, s2, e2) == expected


def test_cspvalidator_allows_an_overlap_the_raw_sql_pattern_would_flag():
    """Demonstrates divergence #2: same faculty, two NSTP/OU sections,
    genuinely overlapping time -- CSPValidator's HC10 explicitly allows this
    (see scheduler.py's _check_faculty_overlaps NSTP/OU exception), but
    api_requests_validate's fc1/fc2 and api_faculty_check_request_conflicts'
    raw overlap SQL have no such exception and would report a conflict.
    If either endpoint were migrated to call ConstraintService as a drop-in
    replacement, this exact case would silently flip from "blocked" to
    "allowed" for end users -- a real behavior change, not a refactor."""
    a = {
        'subject_code': 'NSTP101', 'faculty_id': 'F1', 'room_id': 1,
        'day': 'Monday', 'days_list': ['Monday'],
        'start_time': time(9, 0), 'end_time': time(10, 30),
    }
    b = {
        'subject_code': 'NSTP102', 'faculty_id': 'F1', 'room_id': 2,
        'day': 'Monday', 'days_list': ['Monday'],
        'start_time': time(9, 0), 'end_time': time(10, 30),
    }
    # Authoritative CSPValidator: no HC10 violation (NSTP/OU exception).
    violations = CSPValidator(config={}).validate([a, b], {})
    assert 'HC10' not in {v['rule'] for v in violations}
    # But the raw predicate Requests/Local Arrangements use, applied to the
    # same two faculty-conflict candidates with no exception logic, says
    # they DO overlap -- exactly what those endpoints would report today.
    assert _overlap_via_sql_style(
        a['start_time'], a['end_time'], b['start_time'], b['end_time']
    ) is True
