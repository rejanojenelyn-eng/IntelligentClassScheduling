"""Generation / Retrieve Previous schedule the REGULAR curriculum only.

A program's Regular and Bridging curricula can share a curriculumyear (both
"2022-2023"). Selecting subjects by (program, curriculumyear) alone pulled the
Bridging-only subjects into generated schedules as "ghost" rows that are not in
the Manual Editor's (Regular) Curriculum Guide, and duplicated shared subjects.
Bridging subjects are scheduled only after the Academic Head explicitly switches a
section to Bridging in the Manual Editor.
"""
import inspect
from pathlib import Path

import pytest

import scheduler
from app import query_db
from scheduler import REGULAR_CURRICULUM_SQL

APP_SRC = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')


def test_every_subject_universe_query_is_regular_only():
    assert 'REGULAR_CURRICULUM_SQL' in inspect.getsource(scheduler.ScheduleGenerator.fetch_data) \
        if hasattr(scheduler, 'ScheduleGenerator') else 'REGULAR_CURRICULUM_SQL' in inspect.getsource(scheduler)
    src = inspect.getsource(scheduler)
    assert src.count('+ REGULAR_CURRICULUM_SQL +') >= 2            # fetch_data + fetch_historical_schedule
    fn = APP_SRC[APP_SRC.index('def _resolve_current_curriculum_subjects'):]
    fn = fn[:fn.index('\ndef ', 10)]
    assert 'REGULAR_CURRICULUM_SQL' in fn


def _pairs_with_bridging():
    return query_db("""
        SELECT r.programcode, r.curriculumyear
        FROM curriculum r JOIN curriculum b
          ON UPPER(b.programcode) = UPPER(r.programcode) AND b.curriculumyear = r.curriculumyear
        WHERE r.curriculumtype IS DISTINCT FROM 'WITH_BRIDGING' AND b.curriculumtype = 'WITH_BRIDGING'
    """) or []


def test_regular_filter_drops_bridging_only_and_duplicate_subjects():
    pairs = _pairs_with_bridging()
    if not pairs:
        pytest.skip('no program has both a Regular and a Bridging curriculum of the same year')
    for p in pairs:
        rows = query_db("""
            SELECT UPPER(cs.subjectcode) AS code, cs.yearlevel, cs.semester,
                   c.curriculumtype, COALESCE(cs.isbridging, FALSE) AS isbridging
            FROM curriculumsubject cs JOIN curriculum c ON cs.curriculumid = c.curriculumid
            WHERE c.programcode = %s AND c.curriculumyear = %s AND """ + REGULAR_CURRICULUM_SQL,
            (p['programcode'], p['curriculumyear'])) or []
        assert all(r['curriculumtype'] != 'WITH_BRIDGING' for r in rows), p
        keys = [(r['code'], r['yearlevel'], r['semester']) for r in rows]
        assert len(keys) == len(set(keys)), (p, 'duplicate subjects')


def test_bridging_is_used_when_it_is_the_only_curriculum_of_that_year():
    lone = query_db("""
        SELECT b.programcode, b.curriculumyear FROM curriculum b
        WHERE b.curriculumtype = 'WITH_BRIDGING' AND NOT EXISTS (
            SELECT 1 FROM curriculum r WHERE UPPER(r.programcode) = UPPER(b.programcode)
              AND r.curriculumyear = b.curriculumyear AND r.curriculumtype IS DISTINCT FROM 'WITH_BRIDGING')
        LIMIT 1""")
    if not lone:
        pytest.skip('every Bridging curriculum has a Regular sibling')
    p = lone[0]
    n = query_db("""SELECT COUNT(*) AS n FROM curriculumsubject cs JOIN curriculum c ON cs.curriculumid = c.curriculumid
                    WHERE c.programcode = %s AND c.curriculumyear = %s AND """ + REGULAR_CURRICULUM_SQL,
                 (p['programcode'], p['curriculumyear']), one=True)['n']
    assert n > 0
