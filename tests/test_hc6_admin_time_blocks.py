"""
HC6 Standard Time-Slot Compliance: the admin's blocks (Settings → HC6, hc_time_slots)
are ANCHORS. A class that starts at an anchor's start time must end at that anchor's
end time (3:00 PM -> 6:00 PM); a class starting at any other time may end at any time.
Generation applies the anchors to its own placement times.
"""
import json
import uuid
from datetime import time, datetime

import pytest

import scheduler
from scheduler import CSPValidator, get_blocks_for_hours, refresh_time_blocks, hc6_time_problem
from conftest import requires_db

ANCHOR_3_TO_6 = json.dumps([[15, 0, 18, 0]])


@pytest.fixture
def restore_blocks():
    yield
    refresh_time_blocks({})


def _hc6(code, start, end, slots=ANCHOR_3_TO_6):
    row = {'subject_code': code, 'day': 'Monday', 'days_list': ['Monday'], 'start_time': start, 'end_time': end}
    return CSPValidator(config={'hc_time_slots': slots})._check_standard_slots([row])


def test_a_class_starting_at_an_anchor_must_end_at_its_end():
    assert _hc6('ACCO 014', time(15, 0), time(18, 0)) == []
    v = _hc6('ACCO 014', time(15, 0), time(16, 30))
    assert len(v) == 1 and 'starting at 03:00 PM must end at 06:00 PM' in v[0]['detail']


def test_every_other_start_is_free():
    for s, e in ((time(14, 0), time(17, 0)), (time(8, 0), time(11, 0)), (time(16, 0), time(18, 0)),
                 (time(13, 30), time(15, 0)), (time(7, 30), time(8, 0))):
        assert _hc6('ACCO 014', s, e) == [], (s, e)


def test_no_anchors_means_every_start_and_end_is_free():
    assert _hc6('ACCO 014', time(15, 0), time(16, 30), slots='[]') == []


def test_scheduling_hours_and_nstp_session_still_apply():
    assert 'outside the scheduling hours' in _hc6('ACCO 014', time(19, 0), time(22, 0))[0]['detail']
    assert '3-hour session' in _hc6('NSTP 001', time(14, 0), time(16, 0))[0]['detail']
    assert _hc6('NSTP 001', time(14, 0), time(17, 0)) == []


def test_two_anchors_on_one_start_allow_either_end():
    anchors = [(time(15, 0), time(18, 0)), (time(15, 0), time(16, 30))]
    assert hc6_time_problem('X', time(15, 0), time(16, 30), anchors) is None
    assert 'must end at 04:30 PM or 06:00 PM' in hc6_time_problem('X', time(15, 0), time(17, 0), anchors)


def test_generation_applies_the_anchors_to_its_own_times(restore_blocks):
    refresh_time_blocks({'hc_time_slots': ANCHOR_3_TO_6, 'hc_time_blocks_enabled': 1})
    at_3pm = [b for b in scheduler.STANDARD_BLOCKS if b[0] == time(15, 0)]
    assert at_3pm == [(time(15, 0), time(18, 0))]
    assert (time(15, 0), time(18, 0)) in get_blocks_for_hours(3)
    assert all(b[0] != time(15, 0) for b in get_blocks_for_hours(1.5))
    assert (time(7, 30), time(9, 0)) in get_blocks_for_hours(1.5)        # everything else unchanged
    assert scheduler._VALID_BLOCK_SET == set(scheduler.STANDARD_BLOCKS)


def test_generation_ignores_anchors_while_hc6_is_off(restore_blocks):
    refresh_time_blocks({'hc_time_slots': ANCHOR_3_TO_6, 'hc_time_blocks_enabled': 0})
    assert scheduler.STANDARD_BLOCKS == scheduler._GENERATOR_BLOCKS


def test_empty_or_broken_list_leaves_the_generator_times(restore_blocks):
    for cfg in ({'hc_time_slots': '[]'}, {'hc_time_slots': 'nope'}, {}):
        refresh_time_blocks(cfg)
        assert scheduler.STANDARD_BLOCKS == scheduler._GENERATOR_BLOCKS


@pytest.fixture
def admin(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    import app as app_module
    from psycopg2.extras import RealDictCursor
    cur = t.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Admin' ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        t.close(); pytest.skip('no active Admin account')
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Admin', username=acct['username'], last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex)
    yield c, t
    t.close()


def _saved(t):
    cur = t.raw.cursor()
    cur.execute("SELECT config_value FROM scheduler_config WHERE config_key = 'hc_time_slots'")
    r = cur.fetchone()
    return json.loads(r[0]) if r else None


@requires_db
def test_settings_save_validates_and_normalizes(admin):
    c, t = admin
    before = _saved(t)
    for bad in ([[10, 30, 7, 30]], [[7, 15, 8, 45]], [[6, 0, 9, 0]]):
        r = c.post('/admin/settings/hard_constraints', json={'hc_time_slots': json.dumps(bad)})
        assert r.status_code == 400, bad
        assert _saved(t) == before
    r = c.post('/admin/settings/hard_constraints',
               json={'hc_time_slots': json.dumps([[15, 0, 18, 0], [7, 30, 10, 30], [7, 30, 10, 30]])})
    assert r.status_code == 200
    assert _saved(t) == [[7, 30, 10, 30], [15, 0, 18, 0]]
    assert c.post('/admin/settings/hard_constraints', json={'hc_time_slots': '[]'}).status_code == 200
    assert _saved(t) == []
