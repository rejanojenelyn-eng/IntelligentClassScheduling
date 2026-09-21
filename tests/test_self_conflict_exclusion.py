"""
Integration test for self-conflict prevention during regeneration (architecture
spec section 9): fetch_published_room_faculty_slots's exclude_subject_codes
parameter must exclude ONLY the subject(s) being regenerated from the
Published/Draft occupancy it returns, while everything else for the same
program/year (and everything for other programs/years) still blocks.

Inserts synthetic Published sessions and explicitly DELETEs them again in a
finally block. NOTE: this test commits its fixture rows (fetch_published_
room_faculty_slots reads through a separate pooled connection via
database.query_db, so an uncommitted row in this test's own connection
would be invisible to it) — cleanup is therefore explicit DELETE statements
keyed by a version_number far outside real revision history, not a rollback.
"""
from datetime import time

import psycopg2.extras

from conftest import requires_db


@requires_db
def test_exclude_subject_codes_removes_only_that_subjects_own_slot():
    import app
    from config import Config

    conn = psycopg2.connect(
        dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
        host=Config.DB_HOST, port=Config.DB_PORT,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Initialized before the try so the finally block's cleanup is always safe,
    # even if an early query in the try block itself raises.
    test_version = None
    created_schedule_ids = []
    try:
        # Two real subjects in the same program/year/semester, faked into a
        # Published state with distinct, non-overlapping room/faculty slots.
        cur.execute("""
            SELECT cs.curriculumsubjectid, cs.subjectcode
            FROM curriculumsubject cs JOIN curriculum c ON cs.curriculumid = c.curriculumid
            WHERE c.programcode = 'BSIT' AND c.curriculumyear = '2025-2026'
              AND cs.yearlevel = 4 AND cs.semester = 'B'
            ORDER BY cs.subjectcode
        """)
        subj_rows = cur.fetchall()
        assert len(subj_rows) >= 2, "fixture assumption: BSIT Y4 2025-2026 B has >= 2 subjects"
        target_code = subj_rows[0]["subjectcode"]
        other_code  = subj_rows[1]["subjectcode"]

        cur.execute("SELECT sectionid FROM sections LIMIT 1")
        section_id = cur.fetchone()["sectionid"]
        cur.execute("SELECT roomid FROM room LIMIT 1")
        room_id = cur.fetchone()["roomid"]
        cur.execute("SELECT COALESCE(MAX(version_number), 0) + 2000 AS v FROM schedule_version")
        test_version = cur.fetchone()["v"]

        def _fake_published(cs_id, code):
            cur.execute("""
                INSERT INTO public.schedule (curriculumsubjectid, sectionid, employeenumber, semesterid, datecreated)
                VALUES (%s, %s, NULL, 5, NOW()) RETURNING scheduleid
            """, (cs_id, section_id))
            sched_id = cur.fetchone()["scheduleid"]
            created_schedule_ids.append(sched_id)
            cur.execute("""
                INSERT INTO public.schedule_version (scheduleid, version_number, status, datecreated, source)
                VALUES (%s, %s, 'Published', NOW(), 'official') RETURNING versionid
            """, (sched_id, test_version))
            ver_id = cur.fetchone()["versionid"]
            cur.execute("""
                INSERT INTO public.schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid)
                VALUES (%s, 'Monday', 1, 3, %s)
            """, (ver_id, room_id))

        for row in subj_rows[:2]:
            _fake_published(row["curriculumsubjectid"], row["subjectcode"])
        conn.commit()  # fetch_published_room_faculty_slots reads via query_db's own
                        # pooled connection, which needs the data actually committed
                        # to see it — explicitly DELETEd in the finally block below
                        # instead of relying on a rollback (which cannot undo a
                        # commit that already happened).

        # NOTE on exclude_subject_codes=[] : per the function's own docstring/
        # implementation, an EMPTY list (like None) means "exclude the whole
        # section" (`not excl_codes` is True either way) — that's the "legacy
        # call" / normal generate_draft() behavior (it always passes the
        # curriculum's full subject-code list). To get a true "nothing
        # excluded" baseline for this test we instead pass a non-matching
        # exclude_program so the exclusion branch never triggers at all.
        room_slots_baseline, _ = app.scheduler_engine.fetch_published_room_faculty_slots(
            "B", "AY2526", exclude_program="__NONE__", exclude_year_level=4,
        )
        room_slots_target_excluded, _ = app.scheduler_engine.fetch_published_room_faculty_slots(
            "B", "AY2526", exclude_program="BSIT", exclude_year_level=4,
            exclude_subject_codes=[target_code],
        )

        key = (room_id, "Monday")
        slots_baseline = room_slots_baseline.get(key, [])
        slots_target = room_slots_target_excluded.get(key, [])

        # With no exclusion applying at all, BOTH fake sessions occupy this
        # room/day/time.
        assert len(slots_baseline) == 2
        # With the target subject excluded (as generate_draft does for the
        # curriculum currently being regenerated), only the OTHER subject's
        # slot still blocks — the regenerating subject's own prior session
        # must never be treated as a conflict against itself.
        assert len(slots_target) == 1
    finally:
        # Explicit cleanup (the fixture rows were committed above so this test's
        # own connection can see the data change through query_db's separate
        # pooled connection) — delete sessions, then versions, then schedule
        # rows, then commit the cleanup itself.
        try:
            if test_version is not None:
                cur.execute("""
                    DELETE FROM public.schedule_sessions WHERE versionid IN (
                        SELECT versionid FROM public.schedule_version WHERE version_number = %s
                    )
                """, (test_version,))
                cur.execute("DELETE FROM public.schedule_version WHERE version_number = %s", (test_version,))
            if created_schedule_ids:
                cur.execute("DELETE FROM public.schedule WHERE scheduleid = ANY(%s)", (created_schedule_ids,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
