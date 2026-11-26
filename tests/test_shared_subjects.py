"""Settings > Shared Subjects: a subject split between several faculty, a faculty per slice.

Storage: schedule_sessions.employeenumber is the slice's own faculty (NULL = the subject's
faculty). The DB tests write inside one transaction and always roll it back.
"""
import pytest
from psycopg2.extras import RealDictCursor

import shared_subjects as ss
from conftest import DB_AVAILABLE, signed_in_client

needs_db = pytest.mark.skipif(not DB_AVAILABLE, reason='database not reachable')


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_slice_faculty_only_for_shared_subjects():
    table = {'CPE 311': 2}
    assert ss.slice_faculty('cpe  311', '10043', table) == '10043'
    assert ss.slice_faculty('CPE 311', 'TBA', table) is None
    assert ss.slice_faculty('CPE 311', '', table) is None
    assert ss.slice_faculty('GEED 001', '10043', table) is None   # not shared: follows the subject


def test_main_faculty_is_the_one_with_most_slices():
    table = {'CPE 311': 2}
    rows = [{'subject_code': 'CPE 311', 'faculty_id': 'B'},
            {'subject_code': 'CPE 311', 'faculty_id': 'A'},
            {'subject_code': 'CPE 311', 'faculty_id': 'A'},
            {'subject_code': 'GEED 001', 'faculty_id': 'Z'}]
    assert ss.main_faculty(rows, table) == {'CPE 311': 'A'}
    assert ss.main_faculty(rows[:2], table) == {'CPE 311': 'B'}          # tie: first seen
    paired = [{'subject_code': 'CPE 311', 'faculty_id': 'B', 'days_list': ['Monday', 'Thursday']},
              {'subject_code': 'CPE 311', 'faculty_id': 'A'}]
    assert ss.main_faculty(paired, table) == {'CPE 311': 'B'}            # counts meeting days


def test_faculty_count_is_per_subject_and_section():
    table = {'CPE 311': 2}
    rows = [{'subject_code': 'CPE 311', 'faculty_id': f, 'section_id': 1} for f in ('A', 'B', 'C')]
    assert ss.check_faculty_count(rows, table) == [
        {'subjectcode': 'CPE 311', 'section': '1', 'count': 3, 'limit': 2}]
    two_sections = [{'subject_code': 'CPE 311', 'faculty_id': f, 'section_id': s}
                    for f, s in (('A', 1), ('B', 1), ('C', 2))]
    assert ss.check_faculty_count(two_sections, table) == []
    assert ss.check_faculty_count(rows, {}) == []                        # not shared: never limited


# ── save + read back (rolled back) ───────────────────────────────────────────

@pytest.fixture
def tx():
    import app as A
    conn = A.get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield A, cur
    finally:
        conn.rollback()
        conn.close()


def _target(cur):
    cur.execute("""
        SELECT cs.subjectcode, cs.yearlevel, cs.semester, c.programcode, sec.sectionid,
               sem.semesterid, sem.academicyearid, sem.semestertype
        FROM curriculumsubject cs
        JOIN curriculum c ON c.curriculumid = cs.curriculumid
        JOIN program_yearlevel pyl ON pyl.programcode = c.programcode AND pyl.yearlevel = cs.yearlevel
        JOIN sections sec ON sec.programyearlevelid = pyl.programyearlevelid
        JOIN semester sem ON sem.academicyearid = pyl.academicyearid AND sem.semestertype = cs.semester
        WHERE cs.creditunits = 3 ORDER BY cs.curriculumsubjectid LIMIT 1""")
    t = cur.fetchone()
    cur.execute("SELECT employeenumber FROM faculty ORDER BY employeenumber LIMIT 2")
    facs = [r['employeenumber'] for r in cur.fetchall()]
    if not t or len(facs) < 2:
        pytest.skip('no subject/section/faculty to test with')
    # Clear this subject's schedule in the section so the read-back sees only our rows.
    cur.execute("""
        DELETE FROM schedule_version WHERE scheduleid IN (
            SELECT s.scheduleid FROM schedule s
            JOIN curriculumsubject cs ON cs.curriculumsubjectid = s.curriculumsubjectid
            WHERE s.sectionid = %s AND s.semesterid = %s AND UPPER(cs.subjectcode) = UPPER(%s))""",
                (t['sectionid'], t['semesterid'], t['subjectcode']))
    return t, facs


def _rows(t, f1, f2):
    base = dict(subjectcode=t['subjectcode'], semester=t['semester'], room_id=None)
    return [dict(base, faculty_id=f2, daydesc='Friday', start_time='13:00', end_time='16:00'),
            dict(base, faculty_id=f1, daydesc='Monday', start_time='07:30', end_time='09:00'),
            dict(base, faculty_id=f1, daydesc='Wednesday', start_time='07:30', end_time='09:00')]


def _saved(cur, t):
    cur.execute(f"""
        SELECT ss.daydesc, ss.employeenumber AS own, sv.employeenumber AS main,
               {ss.fac_sql()} AS faculty
        FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid
        JOIN schedule sc ON sc.scheduleid = sv.scheduleid
        WHERE sv.version_number = 999 AND sc.sectionid = %s ORDER BY ss.daydesc""", (t['sectionid'],))
    return {r['daydesc']: r for r in cur.fetchall()}


@needs_db
def test_shared_subject_saves_a_faculty_per_slice_and_splits_load(tx):
    A, cur = tx
    import faculty_load as fl
    t, (f1, f2) = _target(cur)
    cur.execute("INSERT INTO shared_subject_policy (subjectcode, max_faculty) VALUES (%s, 2)",
                (ss.norm_code(t['subjectcode']),))
    A._insert_batch(cur, _rows(t, f1, f2), t['semesterid'], 'Published', 999,
                    t['programcode'], t['yearlevel'], section_id=t['sectionid'])

    saved = _saved(cur, t)
    # f1 teaches 2 of the 3 slices, so f1 is the subject's faculty even though f2 came first.
    assert {d: r['main'] for d, r in saved.items()} == {'Friday': f1, 'Monday': f1, 'Wednesday': f1}
    assert saved['Friday']['own'] == f2 and saved['Monday']['own'] is None
    assert {d: r['faculty'] for d, r in saved.items()} == {'Friday': f2, 'Monday': f1, 'Wednesday': f1}

    # Load: each faculty gets only their slices' hours; the 3 units split 3h:3h.
    for fac, hrs in ((f1, 3.0), (f2, 3.0)):
        rows = [r for r in fl.get_faculty_sessions(cur, fac, t['academicyearid'], t['semestertype'])
                if r['subjectcode'] == t['subjectcode'] and r['sectionid'] == t['sectionid']]
        [g] = fl.group_assignments(rows)
        assert float(g['hrs']) == hrs
        assert g['units'] == 1.5


@needs_db
def test_ordinary_subject_keeps_one_faculty_for_every_slice(tx):
    A, cur = tx
    import faculty_load as fl
    t, (f1, f2) = _target(cur)
    cur.execute("DELETE FROM shared_subject_policy WHERE subjectcode = %s", (ss.norm_code(t['subjectcode']),))
    A._insert_batch(cur, _rows(t, f1, f2), t['semesterid'], 'Published', 999,
                    t['programcode'], t['yearlevel'], section_id=t['sectionid'])
    saved = _saved(cur, t)
    assert all(r['own'] is None for r in saved.values())             # nothing stored per slice
    assert {r['faculty'] for r in saved.values()} == {f2}             # first row's faculty, as before
    rows = [r for r in fl.get_faculty_sessions(cur, f2, t['academicyearid'], t['semestertype'])
            if r['subjectcode'] == t['subjectcode'] and r['sectionid'] == t['sectionid']]
    assert rows and all(r['units'] == 3 and not r.get('shared_subject') for r in rows)


# ── routes (validation only: nothing is written) ─────────────────────────────

@needs_db
def test_settings_lists_subjects_and_rejects_bad_limits():
    c = signed_in_client('Admin')
    data = c.get('/admin/settings/shared_subjects').get_json()
    assert data['success'] and isinstance(data['policies'], list) and data['subjects']
    code = data['subjects'][0]['subjectcode']
    for bad in (1, 11, 'x', None):
        r = c.post('/admin/settings/shared_subjects', json={'subjectcode': code, 'max_faculty': bad})
        assert r.status_code == 400 and not r.get_json()['success']
    r = c.post('/admin/settings/shared_subjects', json={'subjectcode': 'NO SUCH 999', 'max_faculty': 2})
    assert r.status_code == 404


@needs_db
def test_shared_subject_limits_api_and_role_guard():
    data = signed_in_client('Academic Head').get('/api/shared_subjects').get_json()
    assert data['success'] and isinstance(data['limits'], dict)
    import app as A
    r = A.app.test_client().get('/admin/settings/shared_subjects')
    assert r.status_code in (302, 401, 403)


@needs_db
def test_settings_subjects_carry_their_program_for_the_grouped_picker():
    data = signed_in_client('Admin').get('/admin/settings/shared_subjects').get_json()
    s = data['subjects'][0]
    assert s['programcode'] and 'programname' in s and s['subjectcode']
    assert all(isinstance(p.get('programs'), list) for p in data['policies'])


@needs_db
def test_publish_refuses_more_faculty_than_the_subject_allows(monkeypatch):
    # Approve (Publish) applies the same per-section limit as Save as Draft, before any write.
    monkeypatch.setattr(ss, 'limits', lambda: {'CPE 311': 2})
    rows = [{'subject_code': 'CPE 311', 'faculty_id': f, 'section_id': 1, 'day': 'Monday',
             'start_time': '07:30 AM', 'end_time': '09:00 AM', 'room_id': 'TBA'} for f in ('A', 'B', 'C')]
    r = signed_in_client('Academic Head').post('/api/schedule/approve', json={
        'schedule_data': rows,
        'context': {'program': 'BSCPE', 'yearLevel': '3', 'term': 'A', 'acadYear': 'AY2627', 'sectionId': 1}})
    body = r.get_json()
    assert r.status_code == 400 and 'Cannot publish: CPE 311 has 3 faculty' in body['error']
