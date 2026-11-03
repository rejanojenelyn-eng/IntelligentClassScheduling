"""A Pending make-up request whose date + start time passed becomes 'Expired' — not
Rejected (nobody decided). It can no longer be approved, rejected or edited; the faculty
can delete it or submit a new one. DB-backed tests run in one rolled-back transaction."""
import datetime

import pytest

import app as app_module
from conftest import requires_db
from test_bulk_delete_safety import head  # noqa: F401  (fixture)
from test_local_override_snapshot_repair import tx, ls, _cur  # noqa: F401  (fixtures)

pytestmark = requires_db


def _request(tx, ls, when, status='Pending'):
    """A make-up request of the fixture's class on `when` (a date), at its first slot."""
    app_module._apply_request_tables()          # the 'Expired' status constraint
    o = ls['occ'][0]
    cur = _cur(tx)
    cur.execute("""INSERT INTO class_meeting_request (scheduleid, requested_date, new_starttimeid,
                       new_endtimeid, new_roomid, reason, submitted_by, status)
                   VALUES (%s, %s, %s, %s, %s, 'x', %s, %s) RETURNING requestid""",
                (o['scheduleid'], when, o['starttimeid'], o['endtimeid'], o['roomid'], o['fac'], status))
    rid = cur.fetchone()['requestid']
    tx.handle().commit()
    return rid


def _status(tx, rid):
    cur = _cur(tx)
    cur.execute("SELECT status, remarks, reviewed_by FROM class_meeting_request WHERE requestid = %s", [rid])
    return cur.fetchone()


def test_past_pending_request_expires_and_future_one_stays(tx, ls):
    past = _request(tx, ls, datetime.date.today() - datetime.timedelta(days=2))
    future = _request(tx, ls, datetime.date.today() + datetime.timedelta(days=30))
    cur = _cur(tx)
    app_module._expire_past_makeup_requests(cur)
    row = _status(tx, past)
    assert row['status'] == 'Expired' and row['reviewed_by'] is None          # nobody decided
    assert row['remarks'] == app_module.MAKEUP_EXPIRED_REMARK
    assert _status(tx, future)['status'] == 'Pending'


def test_decided_requests_never_expire(tx, ls):
    rid = _request(tx, ls, datetime.date.today() - datetime.timedelta(days=2), status='Cancelled')
    app_module._expire_past_makeup_requests(_cur(tx))
    assert _status(tx, rid)['status'] == 'Cancelled'


@pytest.mark.parametrize('decision', ['Approved', 'Rejected'])
def test_a_past_request_cannot_be_decided(tx, ls, head, decision):
    rid = _request(tx, ls, datetime.date.today() - datetime.timedelta(days=1))
    r = head.post('/api/requests/decide', json={'type': 'makeup', 'id': rid,
                                                'decision': decision, 'remarks': ''})
    body = r.get_json()
    assert r.status_code == 409 and body['code'] == 'REQUEST_EXPIRED', body
    assert _status(tx, rid)['status'] == 'Expired'


def test_an_expired_request_cannot_be_edited(tx, ls):
    rid = _request(tx, ls, datetime.date.today() - datetime.timedelta(days=1))
    with app_module.app.test_request_context():
        resp = app_module._save_makeup_request(
            {'request_date': (datetime.date.today() + datetime.timedelta(days=7)).isoformat()},
            ls['occ'][0]['fac'], existing_requestid=rid)
    resp, status = resp if isinstance(resp, tuple) else (resp, resp.status_code)
    assert status == 409 and 'expired' in (resp.get_json().get('message') or resp.get_json().get('error') or '').lower()
    assert _status(tx, rid)['status'] == 'Expired'


def test_hub_counts_expired_with_rejected_and_cancelled():
    src = app_module.APP_SRC if hasattr(app_module, 'APP_SRC') else open(app_module.__file__, encoding='utf-8').read()
    hub = src[src.index('def api_requests_list'):]
    hub = hub[:hub.index('\n@app.route(')]
    assert "('Rejected', 'Cancelled', 'Expired')" in hub
