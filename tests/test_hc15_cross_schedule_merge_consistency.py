"""
Phase B checkpoint 5: HC15 (Cross-Schedule Conflict Validation) audit fix.

HC15 has no single implementation (see scheduler.py's module docstring and
constraints/hard_constraints.py) -- it is genuinely scattered across two
app.py call sites: _check_cross_schedule_conflicts (generate-time, advisory,
feeds eligibleForApproval) and api_approve_schedule's own inline cross-check
(publish-time, BLOCKING). This checkpoint found and fixed a real contradiction
between them:

  - _check_cross_schedule_conflicts' room-conflict check had NO merge
    exemption at all -- it flagged a legitimate merged/shared session (e.g.
    two NSTP sections sharing a room/time) as a conflict, which fed straight
    into eligibleForApproval and could disable the Approve button for a
    schedule CSPValidator itself would accept.
  - api_approve_schedule's inline check had a hand-rolled "same subject AND
    same faculty" exemption that (a) didn't respect the configured merge
    scope/pairs -- silently allowing an out-of-scope same-subject/
    same-faculty double-booking through untouched -- and (b) didn't cover an
    NSTP/OU merge sharing DIFFERENT faculty, which is explicitly valid under
    HC16 -- wrongly blocking a legitimate approval.

Both now delegate to the one authoritative merge decision
(constraints.hard_constraints.is_valid_merge -> CSPValidator.is_valid_merge)
instead of each hand-rolling its own exemption. These tests prove the shared
decision behaves correctly for exactly the input shapes both call sites
construct, and (via a fake DB cursor) that _check_cross_schedule_conflicts
itself now skips a valid merge instead of flagging it.
"""
from constraints import hard_constraints


# ── Part 1: the shared decision both call sites now use ────────────────────

def test_is_valid_merge_exempts_nstp_pair_with_different_faculty():
    """The exact case api_approve_schedule's OLD ad-hoc exemption
    (sc_new == sc_ex and fac_new == fac_ex) would have wrongly REJECTED as a
    conflict -- an NSTP/OU merge is explicitly allowed to share different
    faculty (final HC16)."""
    a = {'subject_code': 'NSTP101', 'faculty_id': 'F1'}
    b = {'subject_code': 'NSTP101', 'faculty_id': 'F2'}
    assert hard_constraints.is_valid_merge(a, b, config={}) is True


def test_is_valid_merge_rejects_out_of_scope_same_subject_same_faculty():
    """The exact case api_approve_schedule's OLD ad-hoc exemption would have
    wrongly ALLOWED through unexamined: a non-NSTP subject, same faculty,
    same room/time, but outside the configured merge scope (default
    nstp_only) -- not a sanctioned merge, a genuine double-booking."""
    a = {'subject_code': 'IT101', 'faculty_id': 'F1'}
    b = {'subject_code': 'IT101', 'faculty_id': 'F1'}
    assert hard_constraints.is_valid_merge(a, b, config={'hc_merge_scope': 'nstp_only'}) is False


def test_is_valid_merge_accepts_same_subject_same_faculty_when_in_scope():
    # Baseline both old and new logic already agreed on.
    a = {'subject_code': 'IT101', 'faculty_id': 'F1'}
    b = {'subject_code': 'IT101', 'faculty_id': 'F1'}
    assert hard_constraints.is_valid_merge(
        a, b, config={'hc_merge_scope': 'all_subjects'}
    ) is True


def test_is_valid_merge_rejects_different_subjects():
    # Baseline both call sites' room-conflict checks must still flag.
    a = {'subject_code': 'IT101', 'faculty_id': 'F1'}
    b = {'subject_code': 'IT102', 'faculty_id': 'F1'}
    assert hard_constraints.is_valid_merge(a, b, config={}) is False


# ── Part 2: _check_cross_schedule_conflicts itself, via a fake DB cursor ───

class _FakeCursor:
    def __init__(self, sem_row, published_rows):
        self._sem_row = sem_row
        self._published_rows = published_rows
        self._pending = None

    def execute(self, query, params=None):
        if 'FROM semester' in query:
            self._pending = self._sem_row
        elif 'schedule_sessions' in query:
            self._pending = self._published_rows
        else:
            self._pending = None

    def fetchone(self):
        return self._pending

    def fetchall(self):
        return self._pending or []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def close(self):
        pass


def test_check_cross_schedule_conflicts_skips_a_valid_nstp_merge(monkeypatch):
    import app
    import database
    # Legacy-model behaviour (NSTP auto-merge): pinned explicitly, since the live
    # system now runs the HC16 Merge Group model (P7), where NSTP merges only
    # through a recorded merged class.
    _cfg = dict(database.load_scheduler_config(), hc_merge_model='legacy', hc_merge_enabled=1,
                hc_merge_scope='nstp_only', hc_merge_scope_subjects='[]')
    monkeypatch.setattr(database, 'load_scheduler_config', lambda *a, **k: dict(_cfg))

    published_row = {
        'roomid': 1, 'faculty_id': 'F2', 'day': 'Sunday',
        'start_time': __import__('datetime').time(7, 30),
        'end_time': __import__('datetime').time(9, 0),
        'subjectcode': 'NSTP101', 'programcode': 'BSED', 'yearlevel': 1,
        'roomname': 'Room 1',
    }
    fake_conn = _FakeConn(_FakeCursor({'semesterid': 1}, [published_row]))
    monkeypatch.setattr(app, 'get_db_connection', lambda: fake_conn)

    new_cls = {
        'room_id': 1, 'faculty_id': 'F1', 'days_list': ['Sunday'],
        'start_time': __import__('datetime').time(7, 30),
        'end_time': __import__('datetime').time(9, 0),
        'subject_code': 'NSTP101', 'room': 'Room 1',
    }
    violations, count = app._check_cross_schedule_conflicts(
        [new_cls], 'BSIT', 1, 'B', 'AY2526'
    )
    assert violations == []
    assert count == 0


def test_check_cross_schedule_conflicts_still_flags_a_real_room_conflict(monkeypatch):
    import app

    published_row = {
        'roomid': 1, 'faculty_id': 'F2', 'day': 'Monday',
        'start_time': __import__('datetime').time(7, 30),
        'end_time': __import__('datetime').time(9, 0),
        'subjectcode': 'IT201', 'programcode': 'BSED', 'yearlevel': 1,
        'roomname': 'Room 1',
    }
    fake_conn = _FakeConn(_FakeCursor({'semesterid': 1}, [published_row]))
    monkeypatch.setattr(app, 'get_db_connection', lambda: fake_conn)

    new_cls = {
        'room_id': 1, 'faculty_id': 'F1', 'days_list': ['Monday'],
        'start_time': __import__('datetime').time(7, 30),
        'end_time': __import__('datetime').time(9, 0),
        'subject_code': 'IT101', 'room': 'Room 1',
    }
    violations, count = app._check_cross_schedule_conflicts(
        [new_cls], 'BSIT', 1, 'B', 'AY2526'
    )
    assert count == 1
    assert violations[0]['rule'] == 'HC11'
