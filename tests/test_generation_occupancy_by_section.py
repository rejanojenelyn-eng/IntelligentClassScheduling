"""Generation must block every OTHER section's Published/Draft rooms & faculty.

fetch_published_room_faculty_slots used to skip existing sessions by
program + year level + subject only, so when generating one section, a sibling
section of the same program/year (same subjects) looked free -> the generator
double-booked its room, and the post-generation check reported e.g.
"Room LQ117 is already occupied on Friday by GEED 035 (BEED Yr1)".
With exclude_section_id only the section being generated is skipped.
"""
import pytest

import app
from app import query_db


def _sample_row():
    return query_db("""
        SELECT ss.roomid, ss.daydesc, sc.sectionid, UPPER(cs.subjectcode) AS code,
               UPPER(c.programcode) AS prog, cs.yearlevel, sem.academicyearid, sem.semestertype,
               ts_s.timevalue AS st, ts_e.timevalue AS en
        FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid AND sv.status = 'Published'
        JOIN schedule sc ON sc.scheduleid = sv.scheduleid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = sc.curriculumsubjectid
        JOIN curriculum c ON c.curriculumid = cs.curriculumid
        JOIN semester sem ON sem.semesterid = sc.semesterid
        JOIN timeslot ts_s ON ts_s.timeid = ss.starttimeid
        JOIN timeslot ts_e ON ts_e.timeid = ss.endtimeid
        WHERE ss.roomid IS NOT NULL AND sc.sectionid IS NOT NULL
        LIMIT 1""", one=True)


def test_sibling_section_sessions_still_block():
    r = _sample_row()
    if not r:
        pytest.skip('no Published session with a room and section')
    eng = app.scheduler_engine
    kw = dict(exclude_program=r['prog'], exclude_year_level=r['yearlevel'], exclude_subject_codes=[r['code']])
    key, slot = (r['roomid'], r['daydesc']), (r['st'], r['en'])

    # Generating a DIFFERENT section of the same program/year: the slot must stay blocked.
    other, _ = eng.fetch_published_room_faculty_slots(r['semestertype'], r['academicyearid'],
                                                      exclude_section_id=-1, **kw)
    assert slot in other.get(key, []), 'sibling section booking was treated as free'

    # Generating THIS section: its own session for the regenerated subject is replaced.
    own, _ = eng.fetch_published_room_faculty_slots(r['semestertype'], r['academicyearid'],
                                                    exclude_section_id=r['sectionid'], **kw)
    others_same_slot = query_db("""
        SELECT COUNT(*) AS n FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid AND sv.status IN ('Published', 'Draft')
        JOIN schedule sc ON sc.scheduleid = sv.scheduleid
        JOIN semester sem ON sem.semesterid = sc.semesterid
        JOIN timeslot ts_s ON ts_s.timeid = ss.starttimeid JOIN timeslot ts_e ON ts_e.timeid = ss.endtimeid
        WHERE ss.roomid = %s AND ss.daydesc = %s AND sem.academicyearid = %s AND sem.semestertype = %s
          AND ts_s.timevalue = %s AND ts_e.timevalue = %s AND sc.sectionid IS DISTINCT FROM %s
    """, (r['roomid'], r['daydesc'], r['academicyearid'], r['semestertype'], r['st'], r['en'],
          r['sectionid']), one=True)['n']
    if not others_same_slot:
        assert slot not in own.get(key, [])


def test_generate_draft_passes_the_section():
    import inspect, scheduler
    src = inspect.getsource(scheduler.ScheduleGenerator.generate_draft) \
        if hasattr(scheduler, 'ScheduleGenerator') else inspect.getsource(scheduler)
    assert 'exclude_section_id=section_id,' in src
