"""
Settings → Add Academic Year: the tag is typed (2027-2028 / 27-28 / 2728 — the page
normalizes it), but the server stays strict: 4-digit years one year apart, never in the
past, and the NEXT year in sequence only (2026-2027 -> 2027-2028; 2028-2029 used to slip
through and leave 2027-2028 missing). Runs inside a rolled-back transaction.
"""
import uuid
from datetime import datetime

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db


@pytest.fixture
def tx(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    try:
        yield t
    finally:
        t.close()


@pytest.fixture
def admin(tx):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Admin' ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Admin account')
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Admin', username=acct['username'], last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex)
    return c


def _max_end(tx):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT MAX(yearend) AS m FROM academicyear")
    m = cur.fetchone()['m']
    if m is None or int(m) < datetime.now().year:
        pytest.skip('no current academic-year sequence to extend')
    return int(m)


def _exists(tx, y1, y2):
    cur = tx.raw.cursor()
    cur.execute("SELECT 1 FROM academicyear WHERE academicyearid = %s", (f"AY{str(y1)[2:]}{str(y2)[2:]}",))
    return cur.fetchone() is not None


def _post(admin, y1, y2):
    return admin.post('/admin/settings/upsert_ay', data={
        'year_start': str(y1), 'year_end': str(y2), 'form_action': 'add'})


@requires_db
def test_two_digit_years_are_refused(tx, admin):
    m = _max_end(tx)
    _post(admin, str(m)[2:], str(m + 1)[2:])
    assert not _exists(tx, m, m + 1)


@requires_db
def test_skipping_a_year_is_refused(tx, admin):
    m = _max_end(tx)
    _post(admin, m + 1, m + 2)                 # e.g. 2028-2029 after 2026-2027
    assert not _exists(tx, m + 1, m + 2)


@requires_db
def test_the_next_year_in_sequence_is_accepted(tx, admin):
    m = _max_end(tx)
    _post(admin, m, m + 1)                     # e.g. 2027-2028 after 2026-2027
    assert _exists(tx, m, m + 1)


@requires_db
def test_background_save_returns_the_error_to_the_open_form(tx, admin):
    m = _max_end(tx)
    r = admin.post('/admin/settings/upsert_ay', headers={'X-Requested-With': 'fetch'}, data={
        'year_start': str(m), 'year_end': str(m + 1), 'form_action': 'add',
        'sem1_start': f'{m - 1}-08-01', 'sem1_end': f'{m}-12-15'})
    body = r.get_json()
    assert r.status_code == 200 and body['success'] is False
    assert 'must fall within' in body['error']
    assert not _exists(tx, m, m + 1)
    with admin.session_transaction() as s:              # not left behind for the next page
        assert not [f for f in s.get('_flashes', []) if f[0] == 'error']


@requires_db
def test_background_save_succeeds(tx, admin):
    m = _max_end(tx)
    r = admin.post('/admin/settings/upsert_ay', headers={'X-Requested-With': 'fetch'},
                   data={'year_start': str(m), 'year_end': str(m + 1), 'form_action': 'add'})
    assert r.get_json() == {'success': True}
    assert _exists(tx, m, m + 1)
