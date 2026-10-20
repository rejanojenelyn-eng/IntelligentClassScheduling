"""
Manual Editor: a Published slice can be deleted with its × (after the double
confirmation). The editor does it through the normal Publish path: it reads the
section's live sessions (draft_sessions?status=published) and republishes the
subject without that slice — or removes the subject when it was the last one.
Runs inside a rolled-back transaction.
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
def head(tx):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username, employeenumber FROM accounts WHERE isactive AND role = 'Academic Head' "
                "ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Academic Head account')
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=acct['username'],
                 employeenumber=acct['employeenumber'], last_seen=now, last_active=now,
                 sid=uuid.uuid4().hex)
    return c


def _ctx_with_published_subject(tx, head):
    """A section of the active term with a Published subject that has no pending Draft."""
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT sec.sectionid, pyl.programcode, pyl.yearlevel, sm.academicyearid, sm.semestertype
        FROM schedule s
        JOIN schedule_version v ON v.scheduleid = s.scheduleid AND v.status = 'Published'
             AND v.source IS DISTINCT FROM 'local'
        JOIN sections sec ON sec.sectionid = s.sectionid
        JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        JOIN semester sm ON sm.semesterid = s.semesterid AND sm.isactive
        ORDER BY sec.sectionid
    """)
    for row in cur.fetchall():
        q = {'program': row['programcode'], 'year_level': row['yearlevel'], 'ay_id': row['academicyearid'],
             'sem': row['semestertype'], 'section_id': row['sectionid']}
        pub = head.get('/api/schedule/draft_sessions', query_string=dict(q, status='published')).get_json()
        drafts = head.get('/api/schedule/draft_sessions', query_string=q).get_json()
        busy = {s['subject_code'].upper() for s in drafts.get('sessions', [])} | set(drafts.get('removed_subjects', []))
        for s in pub.get('sessions', []):
            if s['subject_code'].upper() not in busy:
                return q, pub['sessions'], s['subject_code'].upper()
    pytest.skip('no section with a Published subject free of pending Drafts')


def _live(head, q):
    rows = head.get('/api/schedule/draft_sessions', query_string=dict(q, status='published')).get_json()['sessions']
    return {(r['subject_code'].upper(), r['daydesc'], r['starttimeid'], r['room_id'], r['faculty_id']) for r in rows}


@requires_db
def test_published_rows_come_back_publish_ready(tx, head):
    q, sessions, _code = _ctx_with_published_subject(tx, head)
    assert sessions and all(s['status'] == 'Published' and s['versionid'] for s in sessions)


@requires_db
def test_deleting_a_published_slice_republishes_without_it(tx, head):
    q, sessions, code = _ctx_with_published_subject(tx, head)
    before = _live(head, q)
    subj = [s for s in sessions if s['subject_code'].upper() == code]
    target, remaining = subj[0], subj[1:]

    # The direct delete still refuses a live Published slice...
    r = head.post('/api/schedule/delete_session', json={'version_id': target['versionid']}).get_json()
    assert r['code'] == 'PUBLISHED_IMMUTABLE'

    # ...so the editor republishes the subject without it (what _deletePublishedSlice sends).
    body = head.post('/api/schedule/approve', json={
        'schedule_data': remaining, 'removed_subjects': [] if remaining else [code], 'override': True,
        'context': {'program': q['program'], 'yearLevel': q['year_level'], 'term': q['sem'],
                    'acadYear': q['ay_id'], 'sectionId': q['section_id']},
    }).get_json()
    assert body.get('success') is True, body

    after = _live(head, q)
    gone = (code, target['daydesc'], target['starttimeid'], target['room_id'], target['faculty_id'])
    assert gone not in after
    # Everything else in the section is still live, exactly as before.
    assert after == before - {gone}
