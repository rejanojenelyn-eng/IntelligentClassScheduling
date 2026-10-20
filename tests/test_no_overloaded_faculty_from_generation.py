"""
Schedule Generation never hands out an instructor that Save as Draft / Approve would
reject for faculty load. Retrieve Previous used to keep a previous-AY instructor who is
already (nearly) full this term — flagged, but still on the classes — so Save Draft
failed ("faculty load limit exceeded") and the Manual Editor opened with them anyway.
Now such an instructor is released right away (Instructor → TBA, row incomplete) and
Re-generate assigns someone with hours left. Runs inside a rolled-back transaction.
"""
import io
import contextlib
import uuid
from datetime import datetime

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import faculty_load
from conftest import requires_db

CTX = {'program': 'BEED', 'yearLevel': 1, 'term': 'A', 'acadYear': 'AY2627',
       'curriculum': '2022-2023', 'section': '9'}
_PLUCK = ['subject_code', 'class_type', 'course', 'description', 'lec_hours', 'lab_hours', 'units',
          'faculty_id', 'instructor', 'room_id', 'room', 'room_type', 'days_list', 'day', 'time', 'days']


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
def client(tx):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Academic Head account')
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=acct['username'], last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex)
    return c


def _post(client, url, body):
    with contextlib.redirect_stdout(io.StringIO()):
        return client.post(url, json=body).get_json()


def _save_gate(rows):
    with contextlib.redirect_stdout(io.StringIO()):
        return app_module._check_cross_program_faculty_loads(
            app_module._rehydrate_schedule([dict(r) for r in rows]), app_module._load_faculty_map(), 1,
            exclude_program='BEED', exclude_year_level=1, exclude_section_id=9)


@pytest.fixture
def nearly_full(tx, client):
    """The previous-AY schedule's busiest instructor, loaded to cap - 1 hrs in OTHER sections."""
    base = _post(client, '/api/schedule/retrieve-previous', CTX)
    if not base.get('success'):
        pytest.skip('no previous-AY schedule for BEED 1')
    fids = [r['faculty_id'] for r in base['schedule_data'] if r.get('faculty_id')]
    fid = max(set(fids), key=fids.count)
    et = app_module._load_faculty_map()[fid]['employeetype']
    cap = (et.get('regularload') or 0) + (et.get('parttimeload') or 0) + (et.get('teachingsubstitution') or 0)
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT v.versionid FROM schedule_version v JOIN schedule s USING (scheduleid)
                   JOIN semester sm ON sm.semesterid = s.semesterid
                   WHERE sm.isactive AND v.status = 'Published' AND s.sectionid <> 9 ORDER BY v.versionid""")
    for r in cur.fetchall():
        cur.execute("UPDATE schedule_version SET employeenumber = %s WHERE versionid = %s", (fid, r['versionid']))
        got = faculty_load.load_total(faculty_load.get_faculty_load_batch(
            cur, 'AY2627', 'A', exclude_section_id=9, config=app_module.load_scheduler_config()).get(fid))
        if got >= cap - 1:
            return fid
    pytest.skip('could not load the instructor close to their cap')


@requires_db
def test_retrieve_previous_releases_an_instructor_over_their_load(client, nearly_full):
    body = _post(client, '/api/schedule/retrieve-previous', CTX)
    rows = body['schedule_data']
    assert not [r for r in rows if r.get('faculty_id') == nearly_full]          # not kept on any class
    released = [r for r in rows if 'Instructor released' in ' '.join(r.get('incomplete_reason') or [])]
    assert released and all(r['instructor'] == 'TBA' and 'faculty' in r['incomplete_components']
                            for r in released)
    assert body['overload_notices'] and _save_gate(rows) == []                 # Save Draft would accept it
    assert not [c for c in (body['evaluation'] or {}).get('conflicts') or [] if c.get('rule') == 'HC9']


@requires_db
def test_regenerating_the_released_rows_assigns_someone_with_hours_left(client, nearly_full):
    rows = _post(client, '/api/schedule/retrieve-previous', CTX)['schedule_data']
    rel = {r['subject_code'] for r in rows if 'Instructor released' in ' '.join(r.get('incomplete_reason') or [])}
    keep = {'faculty': True, 'room': True, 'day': True, 'time': True, 'schedule': True}
    open_fac = dict(keep, faculty=False)
    payload = [dict({k: r[k] for k in _PLUCK if k in r}, row_key=r['subject_code'],
                    selected=r['subject_code'] in rel, lock=open_fac if r['subject_code'] in rel else keep,
                    user_lock={}) for r in rows]
    body = _post(client, '/api/schedule/generate', dict(CTX, locked_sessions=payload))
    after = body.get('schedule_data') or []
    assert after, body.get('error')
    assert all(r.get('faculty_id') != nearly_full for r in after if r['subject_code'] in rel)
    assert _save_gate(after) == []


def test_release_keeps_rows_the_user_locked(monkeypatch):
    rows = [{'subject_code': 'A', 'faculty_id': 'F', 'instructor': 'F', 'lec_hours': 3, 'lab_hours': 0},
            {'subject_code': 'B', 'faculty_id': 'F', 'instructor': 'F', 'lec_hours': 3, 'lab_hours': 0}]
    calls = iter([[{'faculty_id': 'F', 'faculty_name': 'F', 'current_load': 38.0, 'max_load': 40.0}], []])
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'semesterid': 1})
    monkeypatch.setattr(app_module, '_load_faculty_map', lambda: {})
    monkeypatch.setattr(app_module, '_check_cross_program_faculty_loads', lambda *a, **k: next(calls))
    notes = app_module._release_overloaded_faculty(rows, 'P', 1, 'A', 'AY', 1,
                                                   keep=lambda r: r['subject_code'] == 'A')
    assert rows[0]['faculty_id'] == 'F'                       # locked: never changed
    assert rows[1]['faculty_id'] is None and rows[1]['instructor'] == 'TBA'
    assert notes and 'B' in notes[0]
