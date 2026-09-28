"""
Regression: Local Save failed with "ACCO 201 has an invalid Official occurrence reference."

The editor sent ACCO 201's real schedule_sessions.sessionid, but the Local validators
required the occurrence's version row to BE the single "current Official" versionid.
A Published snapshot is one schedule_version row per subject (all sharing one
version_number), so only the anchor subject's occurrences could ever validate — for
BSA 2 (section 477) the anchor was PATHFIT 3's row 1837 while ACCO 201 lives in 1829.

The validators now accept an occurrence from ANY Published row of the anchor's
snapshot, still pinned to the same section/semester/program/year/subject.
"""
import inspect

import pytest

import app


# ── Static: every occurrence binding uses the snapshot, not one row ────────

@pytest.mark.parametrize('fn', [
    app._validate_local_protected_identity,
    app._get_official_occurrence_faculty,
    app._validate_local_official_session_binding,
])
def test_occurrence_validators_bind_to_the_whole_published_snapshot(fn):
    src = inspect.getsource(fn)
    assert '_OFFICIAL_SNAPSHOT_OF_ANCHOR' in src
    assert 'sv.versionid = %s' not in src


def test_snapshot_condition_is_strict():
    sql = app._OFFICIAL_SNAPSHOT_OF_ANCHOR
    assert "sv.status = 'Published'" in sql
    assert 'anchor.versionid = %s' in sql and "anchor.status = 'Published'" in sql
    assert sql.count('%s') == 1


def test_save_subject_mapping_uses_the_snapshot():
    src = inspect.getsource(app.api_save_local_arrangement)
    block = src[src.index('Authoritative subject/faculty mapping'):src.index('official_subject_faculty = {}')]
    assert '_OFFICIAL_SNAPSHOT_OF_ANCHOR' in block
    assert 'sv.versionid = %s' not in block


def test_bindings_still_pin_section_and_subject():
    for fn in (app._get_official_occurrence_faculty, app._validate_local_official_session_binding):
        src = inspect.getsource(fn)
        assert 'AND s.sectionid = %s' in src
        assert 'AND UPPER(cs.subjectcode) = UPPER(%s)' in src


def test_occurrence_set_validation_uses_the_snapshot():
    src = inspect.getsource(app._validate_local_occurrence_coverage)
    assert '_OFFICIAL_SNAPSHOT_OF_ANCHOR' in src
    assert 'sv.versionid = %s' not in src


def test_restore_anchor_matches_publish_anchor_ordering():
    src = inspect.getsource(app.api_restore_local_arrangement)
    assert 'ORDER BY sv.version_number DESC, sv.versionid DESC' in src


# ── Live DB (read-only): real multi-subject snapshot ───────────────────────

@pytest.fixture(scope='module')
def ro_cursor():
    try:
        import psycopg2
        import psycopg2.extras
        from config import Config as C
        conn = psycopg2.connect(dbname=C.DB_NAME, user=C.DB_USER, password=C.DB_PASS,
                                host=C.DB_HOST, port=C.DB_PORT, connect_timeout=3)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f'database unavailable: {exc}')
    conn.set_session(readonly=True)          # proves the checks never write Official data
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    yield cur
    conn.rollback(); conn.close()


@pytest.fixture(scope='module')
def snapshot(ro_cursor):
    """A section whose current Published snapshot spans several version rows and
    contains a subject that meets more than once a week."""
    ro_cursor.execute("""
        SELECT s.sectionid, s.semesterid, UPPER(c.programcode) AS programcode, cs.yearlevel
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        JOIN curriculum c ON cs.curriculumid = c.curriculumid
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        WHERE sv.status = 'Published' AND sv.source IS DISTINCT FROM 'local'
        GROUP BY 1, 2, 3, 4
        HAVING COUNT(DISTINCT sv.versionid) >= 3
           AND COUNT(ss.sessionid) > COUNT(DISTINCT sv.versionid)
        ORDER BY s.semesterid, s.sectionid
        LIMIT 1
    """)
    scope = ro_cursor.fetchone()
    if not scope:
        pytest.skip('no multi-subject Published snapshot in this database')
    # Same anchor choice as save/publish/check_room_conflicts.
    ro_cursor.execute("""
        SELECT sv.versionid FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        JOIN curriculum c ON cs.curriculumid = c.curriculumid
        WHERE sv.status = 'Published' AND UPPER(c.programcode) = %s AND cs.yearlevel = %s
          AND s.semesterid = %s AND s.sectionid = %s
        ORDER BY sv.version_number DESC, sv.versionid DESC LIMIT 1
    """, (scope['programcode'], scope['yearlevel'], scope['semesterid'], scope['sectionid']))
    anchor = ro_cursor.fetchone()['versionid']
    ro_cursor.execute("""
        SELECT ss.sessionid, ss.versionid, UPPER(cs.subjectcode) AS subjectcode
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE sv.status = 'Published' AND s.sectionid = %s AND s.semesterid = %s
        ORDER BY ss.sessionid
    """, (scope['sectionid'], scope['semesterid']))
    return dict(scope, anchor=anchor, occurrences=ro_cursor.fetchall())


def _args(snap, subject):
    return (snap['anchor'], snap['sectionid'], snap['semesterid'],
            snap['programcode'], snap['yearlevel'], subject)


def test_every_occurrence_of_the_snapshot_binds(ro_cursor, snapshot):
    off_anchor = [o for o in snapshot['occurrences'] if o['versionid'] != snapshot['anchor']]
    assert off_anchor, 'fixture must include subjects outside the anchor row'
    for o in snapshot['occurrences']:
        assert app._validate_local_official_session_binding(
            ro_cursor, o['sessionid'], *_args(snapshot, o['subjectcode'])), o
        ok, err = app._validate_local_protected_identity(
            ro_cursor, {'official_sessionid': o['sessionid'], 'subject_code': o['subjectcode']},
            snapshot['anchor'], snapshot['sectionid'], snapshot['semesterid'],
            snapshot['programcode'], snapshot['yearlevel'])
        assert ok, (o, err)
        _, faculty_ok = app._get_official_occurrence_faculty(
            ro_cursor, o['sessionid'], *_args(snapshot, o['subjectcode']))
        assert faculty_ok, o


def test_multi_occurrence_subject_has_distinct_ids(snapshot):
    by_subject = {}
    for o in snapshot['occurrences']:
        by_subject.setdefault(o['subjectcode'], []).append(o['sessionid'])
    multi = [ids for ids in by_subject.values() if len(ids) > 1]
    assert multi and all(len(ids) == len(set(ids)) for ids in multi)


def test_nonexistent_occurrence_is_rejected(ro_cursor, snapshot):
    subject = snapshot['occurrences'][0]['subjectcode']
    assert not app._validate_local_official_session_binding(
        ro_cursor, 2_000_000_000, *_args(snapshot, subject))


def test_occurrence_with_the_wrong_subject_is_rejected(ro_cursor, snapshot):
    occ = snapshot['occurrences']
    other = next((o for o in occ if o['subjectcode'] != occ[0]['subjectcode']), None)
    assert other
    assert not app._validate_local_official_session_binding(
        ro_cursor, occ[0]['sessionid'], *_args(snapshot, other['subjectcode']))


def test_occurrence_from_another_section_is_rejected(ro_cursor, snapshot):
    ro_cursor.execute("""
        SELECT ss.sessionid, UPPER(cs.subjectcode) AS subjectcode
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE sv.status = 'Published' AND s.sectionid <> %s
        LIMIT 1
    """, (snapshot['sectionid'],))
    foreign = ro_cursor.fetchone()
    if not foreign:
        pytest.skip('no other section has a Published schedule')
    assert not app._validate_local_official_session_binding(
        ro_cursor, foreign['sessionid'], *_args(snapshot, foreign['subjectcode']))
    ok, _ = app._validate_local_protected_identity(
        ro_cursor, {'official_sessionid': foreign['sessionid'], 'subject_code': foreign['subjectcode']},
        snapshot['anchor'], snapshot['sectionid'], snapshot['semesterid'],
        snapshot['programcode'], snapshot['yearlevel'])
    assert not ok
