"""
Faculty Load → Pending Assignments: an "Assign Faculty" reservation is dropped once its
subject has a real active schedule in that section, whoever teaches it. Before, only
Save as Draft cleared it, so after a reassignment / Generation → Approve the OLD
faculty kept the subject as "Not scheduled" and it still counted in their load.
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


def _world(tx):
    """An active-term subject with a Published schedule in a section, and another faculty."""
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT s.sectionid, s.semesterid, sm.academicyearid, sm.semestertype,
               UPPER(cs.subjectcode) AS code, pyl.programcode, pyl.yearlevel,
               COALESCE(sv.employeenumber, s.employeenumber) AS teacher
        FROM schedule s
        JOIN schedule_version sv ON sv.scheduleid = s.scheduleid AND sv.status = 'Published'
             AND sv.source IS DISTINCT FROM 'local'
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = s.curriculumsubjectid
        JOIN sections sec ON sec.sectionid = s.sectionid
        JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
             AND pyl.curriculumid = cs.curriculumid AND pyl.academicyearid = (
                 SELECT academicyearid FROM semester WHERE semesterid = s.semesterid)
        JOIN semester sm ON sm.semesterid = s.semesterid AND sm.isactive
        ORDER BY s.scheduleid LIMIT 1
    """)
    w = cur.fetchone()
    if not w:
        pytest.skip('no Published schedule in the active term')
    cur.execute("SELECT employeenumber FROM faculty WHERE employeenumber IS DISTINCT FROM %s "
                "AND employeestatus != 'Archive' ORDER BY employeenumber LIMIT 1", (w['teacher'],))
    other = cur.fetchone()
    if not other:
        pytest.skip('no second faculty')
    app_module._ensure_faculty_assignment_table(cur)
    # The OLD faculty's reservation for the same subject + section, left behind.
    cur.execute("""
        INSERT INTO public.subject_faculty_assignment
            (programcode, yearlevel, semesterid, subjectcode, employeenumber, sectionid)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (programcode, yearlevel, semesterid, subjectcode)
        DO UPDATE SET employeenumber = EXCLUDED.employeenumber, sectionid = EXCLUDED.sectionid
    """, (w['programcode'].upper(), w['yearlevel'], w['semesterid'], w['code'],
          other['employeenumber'], w['sectionid']))
    return cur, w, other['employeenumber']


def _reservation_left(cur, w):
    cur.execute("SELECT 1 FROM public.subject_faculty_assignment WHERE semesterid = %s "
                "AND UPPER(subjectcode) = %s", (w['semesterid'], w['code']))
    return cur.fetchone() is not None


@requires_db
def test_teaching_assignments_drop_a_superseded_reservation(tx, client):
    cur, w, old = _world(tx)
    body = client.get('/api/faculty/teaching_assignments', query_string={
        'emp_num': old, 'ay_id': w['academicyearid'], 'sem': w['semestertype']}).get_json()
    assert body['success'] is True, body
    assert w['code'] not in {(p.get('subjectcode') or '').upper() for p in body['pending_sessions']}
    assert not _reservation_left(cur, w)


@requires_db
def test_manual_faculty_load_does_not_count_a_superseded_reservation(tx, client):
    cur, w, old = _world(tx)
    body = client.get('/api/manual/faculty_load', query_string={
        'emp_num': old, 'ay_id': w['academicyearid'], 'sem': w['semestertype']}).get_json()
    assert body['success'] is True, body
    assert w['code'] not in {(p.get('subjectcode') or '').upper() for p in body['pending_subjects']}
    assert not _reservation_left(cur, w)


@requires_db
def test_reservation_without_a_schedule_is_kept(tx, client):
    cur, w, old = _world(tx)
    # Another section, same program/year: the schedule there does not supersede it.
    cur.execute("UPDATE public.subject_faculty_assignment SET sectionid = -1 "
                "WHERE semesterid = %s AND UPPER(subjectcode) = %s", (w['semesterid'], w['code']))
    assert app_module._drop_superseded_faculty_reservations(cur, w['semesterid'], old) == 0
    assert _reservation_left(cur, w)
