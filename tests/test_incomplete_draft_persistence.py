"""
Integration test for _insert_batch's incomplete_map handling (architecture
spec section 14: "A PARTIAL_VALID result may be saved as an Incomplete
Draft"). Runs against the real database inside a transaction that is always
rolled back at the end — nothing is permanently written.
"""
from datetime import time

import psycopg2.extras

from conftest import requires_db


@requires_db
def test_insert_batch_flags_schedule_version_is_incomplete():
    import app  # ensures the is_incomplete/incomplete_components migration ran
    from config import Config

    conn = psycopg2.connect(
        dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
        host=Config.DB_HOST, port=Config.DB_PORT,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT COALESCE(MAX(version_number), 0) + 1000 AS v
            FROM public.schedule_version
        """)
        test_version = cur.fetchone()["v"]  # far outside real revision history

        schedule_data = [{
            "subjectcode": "INTE 404", "employeenumber": None,
            "roomid": None, "start_time": time(7, 30), "end_time": time(9, 0),
            "days_list": ["Monday"], "sem": "B",
        }]
        unsaved = app._insert_batch(
            cur, schedule_data, semester_id=5, target_status="Draft",
            version_number=test_version, program="BSIT", year_level=4,
            source="local", incomplete_map={"INTE 404": ["HC9: room conflict (test)"]},
        )

        cur.execute("""
            SELECT sv.is_incomplete, sv.incomplete_components
            FROM public.schedule_version sv
            JOIN public.schedule s ON sv.scheduleid = s.scheduleid
            JOIN public.curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
            WHERE sv.version_number = %s AND UPPER(cs.subjectcode) = 'INTE 404'
        """, (test_version,))
        row = cur.fetchone()

        assert row is not None, "expected schedule_version row was not created"
        assert row["is_incomplete"] is True
        assert row["incomplete_components"] == ["HC9: room conflict (test)"]
        assert unsaved == set()  # this subject DID get a resolved slice, so it's not "unsaved"
    finally:
        conn.rollback()  # never persist test data
        cur.close()
        conn.close()


@requires_db
def test_insert_batch_reports_subjects_with_zero_resolved_slices_as_unsaved():
    import app
    from config import Config

    conn = psycopg2.connect(
        dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
        host=Config.DB_HOST, port=Config.DB_PORT,
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT COALESCE(MAX(version_number), 0) + 1000 AS v FROM public.schedule_version")
        test_version = cur.fetchone()["v"]

        # No start_time/end_time at all — this slice is skipped by _insert_batch's
        # own completeness check (schedule_sessions has NOT NULL day/time columns),
        # so no schedule_version row can exist for it at all.
        schedule_data = [{
            "subjectcode": "COMP 024", "employeenumber": None,
            "roomid": None, "start_time": None, "end_time": None,
            "days_list": [], "sem": "B",
        }]
        unsaved = app._insert_batch(
            cur, schedule_data, semester_id=5, target_status="Draft",
            version_number=test_version, program="BSIT", year_level=4,
            source="local", incomplete_map={"COMP 024": ["no candidate found (test)"]},
        )
        assert unsaved == {"COMP 024"}
    finally:
        conn.rollback()
        cur.close()
        conn.close()
