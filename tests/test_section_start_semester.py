"""A section DETECTED from a schedule import belongs to that import's academic year
and exists from that import's semester onward (1st -> 2nd -> Summer) -- it is not
offered in that AY's earlier semesters, nor in other AYs. Default / manually added /
pre-existing sections (start_semestertype NULL) cover the whole AY."""
from pathlib import Path

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from app import _mark_section_start_sem, _section_start_sem_for, get_db_connection

APP_SRC = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')

# Same predicate as /api/sections-by-program uses for its `semester` filter.
_VISIBLE_SQL = """
    SELECT sec.sectionid FROM sections sec
    WHERE sec.sectionid = %s
      AND (sec.start_semestertype IS NULL OR sec.start_semestertype <= %s)
"""


def test_start_sem_normalization():
    assert [_section_start_sem_for(x) for x in ('A', 'b', ' C ', '', None, 'X')] == ['A', 'B', 'C', None, None, None]


def test_api_filters_by_starting_semester_and_imports_stamp_it():
    api = APP_SRC[APP_SRC.index('def api_sections_by_program'):]
    api = api[:api.index('\n@app.route(') if '\n@app.route(' in api else len(api)]
    assert '(sec.start_semestertype IS NULL OR sec.start_semestertype <= %s)' in api
    # every auto-detect insert path records the import's semester
    assert APP_SRC.count('INSERT INTO sections (programyearlevelid, sectionname, isactive, start_semestertype)') == 3
    assert "sem_type=data.get('semester_type')" in APP_SRC


@pytest.fixture
def tx():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        conn.rollback(); cur.close(); conn.close()


def _visible(cur, sid, sem):
    cur.execute(_VISIBLE_SQL, (sid, sem))
    return cur.fetchone() is not None


def test_detected_section_shows_from_its_semester_onward(tx):
    cur = tx
    cur.execute("SELECT programyearlevelid FROM program_yearlevel ORDER BY programyearlevelid LIMIT 1")
    pyl = cur.fetchone()
    if not pyl:
        pytest.skip('no program_yearlevel rows')
    cur.execute("""INSERT INTO sections (programyearlevelid, sectionname, isactive, start_semestertype)
                   VALUES (%s, 'ZZTEST-B', TRUE, 'B') RETURNING sectionid""", (pyl['programyearlevelid'],))
    sid = cur.fetchone()['sectionid']
    # detected in the 2nd semester: hidden in 1st, shown in 2nd and Summer
    assert [_visible(cur, sid, s) for s in 'ABC'] == [False, True, True]
    # later found again in a Summer import: start stays at 2nd
    _mark_section_start_sem(cur, sid, 'C')
    assert [_visible(cur, sid, s) for s in 'ABC'] == [False, True, True]
    # found in a 1st-semester import: start moves EARLIER to 1st
    _mark_section_start_sem(cur, sid, 'A')
    assert [_visible(cur, sid, s) for s in 'ABC'] == [True, True, True]

    cur.execute("""INSERT INTO sections (programyearlevelid, sectionname, isactive)
                   VALUES (%s, 'ZZTEST-ALL', TRUE) RETURNING sectionid""", (pyl['programyearlevelid'],))
    whole = cur.fetchone()['sectionid']
    _mark_section_start_sem(cur, whole, 'C')          # a whole-AY section is never narrowed
    assert [_visible(cur, whole, s) for s in 'ABC'] == [True, True, True]


# ── Imports treat 'BSIT1' / 'BSIT 1' / 'BSIT-1' as ONE section ───────────────

from app import _find_section_like, _pick_section_by_name, _section_name_key


def test_section_name_key_ignores_separators_but_not_numbers_or_letters():
    same = ['BSIT1', 'BSIT 1', 'BSIT-1', 'bsit_1', ' BSIT - 1 ', 'BSIT.1']
    assert len({_section_name_key(n) for n in same}) == 1
    different = ['BSIT 1', 'BSIT 1-1', 'BSIT 1-2', 'BSIT 2-1', 'BSIT 11', 'BSIT 1A', 'BSCS 1']
    assert len({_section_name_key(n) for n in different}) == len(different)
    assert _section_name_key('BSIT1-1') == _section_name_key('BSIT 1 - 1')


def test_pick_prefers_exact_then_equivalent_and_active():
    rows = [{'sectionid': 1, 'sectionname': 'BSIT-1', 'isactive': True},
            {'sectionid': 2, 'sectionname': 'BSIT 1-1', 'isactive': True},
            {'sectionid': 3, 'sectionname': 'BSIT1', 'isactive': False}]
    assert _pick_section_by_name(rows, 'BSIT 1')['sectionid'] == 1      # equivalent, active preferred
    assert _pick_section_by_name(rows, 'bsit1')['sectionid'] == 3       # exact (case-insensitive) wins
    assert _pick_section_by_name(rows, 'BSIT1-1')['sectionid'] == 2
    assert _pick_section_by_name(rows, 'BSIT 1-2') is None              # a real new section
    assert _pick_section_by_name(rows, '') is None


def test_import_lookup_reuses_existing_section(tx):
    cur = tx
    cur.execute("SELECT programyearlevelid FROM program_yearlevel ORDER BY programyearlevelid LIMIT 1")
    pyl = cur.fetchone()
    if not pyl:
        pytest.skip('no program_yearlevel rows')
    cur.execute("""INSERT INTO sections (programyearlevelid, sectionname, isactive)
                   VALUES (%s, 'ZQ-9', TRUE) RETURNING sectionid""", (pyl['programyearlevelid'],))
    sid = cur.fetchone()['sectionid']
    for name in ('ZQ9', 'ZQ 9', 'zq-9'):
        assert _find_section_like(cur, pyl['programyearlevelid'], name)['sectionid'] == sid, name
    assert _find_section_like(cur, pyl['programyearlevelid'], 'ZQ 9-1') is None


def test_every_import_path_uses_the_shared_matcher():
    assert APP_SRC.count('_find_section_like(cur, ') >= 3          # historical, import_schedule, SIS confirm
    assert '_pick_section_by_name(cur.fetchall() or [], course_name)' in APP_SRC   # import preview
