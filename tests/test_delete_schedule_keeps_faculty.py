"""Deleting a subject's schedule (calendar pill ×) removes its day/time/room — not its
faculty. When the deleted Draft was the subject's last active schedule in the section,
its faculty is kept as the subject's faculty assignment. Runs in one rolled-back
transaction (nothing persists)."""
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db

pytestmark = requires_db


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
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Academic Head' ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Academic Head account')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=acct['username'])
    return c


def _only_draft(cur, single=True):
    """A Draft version with a faculty that is (single=True) / is not (single=False) its
    subject's only active Draft/Published version in the section."""
    cur.execute(f"""
        SELECT sv.versionid, sv.employeenumber, s.sectionid, s.semesterid,
               UPPER(cs.subjectcode) AS code, UPPER(c.programcode) AS prog, cs.yearlevel
        FROM schedule_version sv
        JOIN schedule s ON s.scheduleid = sv.scheduleid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = s.curriculumsubjectid
        JOIN curriculum c ON c.curriculumid = cs.curriculumid
        WHERE sv.status = 'Draft' AND sv.source IS DISTINCT FROM 'local'
          AND sv.employeenumber IS NOT NULL AND UPPER(sv.employeenumber) <> 'TBA'
          AND (SELECT COUNT(*) FROM schedule_version v2 JOIN schedule s2 ON s2.scheduleid = v2.scheduleid
               JOIN curriculumsubject cs2 ON cs2.curriculumsubjectid = s2.curriculumsubjectid
               WHERE s2.sectionid = s.sectionid AND s2.semesterid = s.semesterid
                 AND UPPER(cs2.subjectcode) = UPPER(cs.subjectcode)
                 AND v2.status IN ('Draft', 'Published') AND v2.source IS DISTINCT FROM 'local')
              {'= 1' if single else '> 1'}
        ORDER BY sv.versionid DESC LIMIT 1
    """)
    return cur.fetchone()


def _assignment(cur, v):
    cur.execute("""SELECT employeenumber FROM subject_faculty_assignment
                   WHERE UPPER(programcode) = %s AND yearlevel = %s AND semesterid = %s
                     AND UPPER(subjectcode) = %s""", (v['prog'], v['yearlevel'], v['semesterid'], v['code']))
    r = cur.fetchone()
    return r['employeenumber'] if r else None


def test_deleting_last_schedule_keeps_its_faculty(tx, head):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    v = _only_draft(cur, single=True)
    if not v:
        pytest.skip('no single-version Draft with a faculty to delete')
    cur.execute("DELETE FROM subject_faculty_assignment WHERE UPPER(programcode) = %s AND yearlevel = %s "
                "AND semesterid = %s AND UPPER(subjectcode) = %s",
                (v['prog'], v['yearlevel'], v['semesterid'], v['code']))
    r = head.post('/api/schedule/delete_session', json={'version_id': v['versionid']}).get_json()
    assert r['success'] and r['whole_version_deleted']
    cur.execute("SELECT status FROM schedule_version WHERE versionid = %s", (v['versionid'],))
    assert cur.fetchone()['status'] == 'Archive'
    assert _assignment(cur, v) == v['employeenumber']


def test_subject_still_scheduled_elsewhere_gets_no_new_assignment(tx, head):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    v = _only_draft(cur, single=True)
    if not v:
        pytest.skip('no Draft with a faculty to delete')
    # Give the subject a second active version in the same section (inside the transaction).
    cur.execute("""INSERT INTO schedule_version (scheduleid, version_number, status, datecreated, source,
                                                original_status, employeenumber)
                   SELECT scheduleid, version_number + 1000, 'Published', NOW(), source, 'Published', employeenumber
                   FROM schedule_version WHERE versionid = %s""", (v['versionid'],))
    cur.execute("DELETE FROM subject_faculty_assignment WHERE UPPER(programcode) = %s AND yearlevel = %s "
                "AND semesterid = %s AND UPPER(subjectcode) = %s",
                (v['prog'], v['yearlevel'], v['semesterid'], v['code']))
    assert head.post('/api/schedule/delete_session', json={'version_id': v['versionid']}).get_json()['success']
    assert _assignment(cur, v) is None
