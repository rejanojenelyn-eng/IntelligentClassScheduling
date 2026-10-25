"""
Generation, last step: a class it could not place on its own (e.g. NSTP when no room or
instructor is free on Sunday) joins ANOTHER section's class of the same subject when that
is a valid merge — same instructor or TBA, exactly the same day, time and room, and this
section free then. Nothing is recorded: Save Draft / Publish ask to confirm the merge.
Read-only against the real database (rolled back).
"""
from datetime import time

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
def nstp(tx):
    """A section of the active semester that offers NSTP 001 but has no schedule for it,
    and another section's Published NSTP 001 class with an instructor and a room."""
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT sc.sectionid AS other, ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid, sc.employeenumber,
               sem.academicyearid, sem.semestertype
        FROM schedule sc
        JOIN schedule_version sv ON sv.scheduleid = sc.scheduleid AND sv.status = 'Published'
             AND sv.source IS DISTINCT FROM 'local'
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        JOIN semester sem ON sem.semesterid = sc.semesterid AND sem.isactive
        WHERE UPPER(cs.subjectcode) = 'NSTP 001' AND ss.roomid IS NOT NULL AND sc.employeenumber IS NOT NULL
        ORDER BY sc.sectionid LIMIT 1
    """)
    other = cur.fetchone()
    if not other:
        pytest.skip('no Published NSTP 001 class with an instructor')
    cur.execute("""
        SELECT sec.sectionid FROM sections sec
        JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        JOIN curriculumsubject cs ON cs.curriculumid = pyl.curriculumid AND UPPER(cs.subjectcode) = 'NSTP 001'
                                 AND cs.semester = %s AND cs.yearlevel = pyl.yearlevel
        WHERE pyl.academicyearid = %s AND sec.isactive AND sec.sectionid <> %s
          AND NOT EXISTS (SELECT 1 FROM schedule s2 JOIN curriculumsubject c2 ON c2.curriculumsubjectid = s2.curriculumsubjectid
                          JOIN semester m2 ON m2.semesterid = s2.semesterid AND m2.isactive
                          WHERE s2.sectionid = sec.sectionid AND UPPER(c2.subjectcode) = 'NSTP 001')
        ORDER BY sec.sectionid LIMIT 1
    """, [other['semestertype'], other['academicyearid'], other['other']])
    me = cur.fetchone()
    if not me:
        pytest.skip('no section without an NSTP 001 schedule')
    return dict(other, me=me['sectionid'])


def _row(fac=None):
    return {'subject_code': 'NSTP 001', 'class_type': 'Lecture', 'lec_hours': 3, 'lab_hours': 0,
            'total_subject_hrs': 3, 'incomplete': True, 'incomplete_components': ['room', 'faculty'],
            'faculty_id': fac, 'instructor': '-', 'room_id': 'TBA', 'room': 'TBA'}


@requires_db
def test_incomplete_class_joins_a_valid_merge(tx, nstp):
    row = _row()
    done = app_module._auto_merge_incomplete([row], nstp['semestertype'], nstp['academicyearid'], nstp['me'])
    assert done and done[0]['subject_code'] == 'NSTP 001'
    assert row['incomplete'] is False and row['merge_with'] and row['day'] and row['room_id'] not in (None, 'TBA')
    assert row['faculty_id']                                  # the merged class's instructor
    # Save Draft / Publish see it as a merge to confirm (never a silent conflict).
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cfg = app_module.load_scheduler_config()
    sem_id = app_module._get_semester_id(cur, nstp['academicyearid'], nstp['semestertype'])
    found = app_module.merge_groups.detect_merges(cur, cfg, sem_id, nstp['me'], [row])
    assert found['merges'] and not found['problems']


@requires_db
def test_never_with_a_different_instructor(tx, nstp):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT employeenumber FROM faculty WHERE employeenumber <> %s ORDER BY employeenumber LIMIT 1",
                [nstp['employeenumber']])
    mine = cur.fetchone()['employeenumber']
    row = _row(fac=mine)
    done = app_module._auto_merge_incomplete([row], nstp['semestertype'], nstp['academicyearid'], nstp['me'])
    # A merge may only join a class of THIS instructor (or one with none yet): the row's
    # instructor never changes.
    assert row['faculty_id'] == mine


@requires_db
def test_a_locked_time_or_room_is_never_moved(tx, nstp):
    row = _row()
    locks = [{'subject_code': 'NSTP 001', 'user_lock': {'time': True}}]
    assert app_module._auto_merge_incomplete([row], nstp['semestertype'], nstp['academicyearid'], nstp['me'],
                                             locks) == []
    assert row['incomplete'] is True


@requires_db
def test_not_when_the_section_is_busy_then(tx, nstp):
    row = _row()
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    blockers = [{'subject_code': f'X{i}', 'incomplete': False, 'days_list': [d], 'day': d,
                 'start_time': time(7, 30), 'end_time': time(21, 0)} for i, d in enumerate(days)]
    assert app_module._auto_merge_incomplete([row] + blockers, nstp['semestertype'],
                                             nstp['academicyearid'], nstp['me']) == []
    assert row['incomplete'] is True
