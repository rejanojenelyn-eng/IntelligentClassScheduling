"""
Local Arrangement = PARTIAL override of Official occurrences — real-database lifecycle.

Runs the real endpoints against the dev database inside ONE transaction that is always
rolled back (the app's commit()/rollback() calls are no-ops on the shared connection),
so nothing persists. Skips when the database is unreachable or has no suitable section.

    effective = Official Published occurrences
                − occurrences overridden by the active Published Local Arrangement
                + that arrangement's Local sessions
"""
import pytest

import app
import database

pytestmark = pytest.mark.filterwarnings('ignore::DeprecationWarning')


class _Shared:
    def __init__(self, real):
        self.real = real

    def cursor(self, *a, **k):
        return self.real.cursor(*a, **k)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture(scope='module')
def env():
    try:
        import psycopg2
        from config import Config as C
        real = psycopg2.connect(dbname=C.DB_NAME, user=C.DB_USER, password=C.DB_PASS,
                                host=C.DB_HOST, port=C.DB_PORT, connect_timeout=3)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f'database unavailable: {exc}')

    mp = pytest.MonkeyPatch()
    shared = _Shared(real)
    real_query = database.query_db
    mp.setattr(app, 'get_db_connection', lambda: shared)
    mp.setattr(database, 'get_db_connection', lambda: shared)
    mp.setattr(app, '_local_tables_ensured', True)
    mp.setattr(app, 'query_db', lambda q, a=(), one=False:
               {'isactive': True} if 'FROM accounts' in q else real_query(q, a, one))
    client = app.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='lifecycle-test')
    try:
        yield client, real
    finally:
        real.rollback()
        real.close()
        mp.undo()


@pytest.fixture(autouse=True)
def _reset_real_transaction_between_tests(env):
    """Keep each lifecycle test isolated on the shared real DB connection.

    The endpoint-facing _Shared wrapper intentionally makes app-level commit()/rollback()
    no-ops so a test cannot persist data.  PostgreSQL transaction state, however, is
    connection-wide: if one test exercises an expected failing statement, the real
    connection can remain INERROR even though the wrapper swallowed rollback().  Reset
    the real transaction after every test so a validation test cannot poison the next
    request.  This still preserves the original safety contract: nothing created by a
    test is persisted.
    """
    try:
        yield
    finally:
        env[1].rollback()


def _q(real, sql, params=()):
    import psycopg2.extras
    cur = real.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    return cur.fetchall()


@pytest.fixture(scope='module')
def scope(env):
    client, real = env
    rows = _q(real, """
        SELECT s.sectionid, s.semesterid, pyl.programcode, pyl.yearlevel, sem.academicyearid, sem.semestertype
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN semester sem ON sem.semesterid = s.semesterid AND sem.isactive
        JOIN sections sec ON sec.sectionid = s.sectionid
        JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        WHERE sv.status = 'Published' AND sv.source IS DISTINCT FROM 'local'
          AND NOT EXISTS (SELECT 1 FROM local_arrangement la
                          WHERE la.sectionid = s.sectionid AND la.semesterid = s.semesterid
                            AND la.status = 'Published' AND la.is_active)
        GROUP BY 1, 2, 3, 4, 5, 6
        HAVING COUNT(DISTINCT sv.versionid) >= 3 AND COUNT(ss.sessionid) > COUNT(DISTINCT sv.versionid)
        ORDER BY 1 LIMIT 1
    """)
    if not rows:
        pytest.skip('no active-semester section with a multi-subject Published snapshot')
    sc = rows[0]
    occ = _q(real, """
        SELECT ss.sessionid, UPPER(cs.subjectcode) AS subjectcode, ss.daydesc,
               ss.starttimeid, ss.endtimeid, ss.roomid
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid AND sv.status = 'Published'
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE s.sectionid = %s AND s.semesterid = %s AND ss.roomid IS NOT NULL
        ORDER BY ss.sessionid
    """, (sc['sectionid'], sc['semesterid']))
    by_subject = {}
    for o in occ:
        by_subject.setdefault(o['subjectcode'], []).append(o)
    multi = next((v for v in by_subject.values() if len(v) >= 2), None)
    single = next((v[0] for k, v in by_subject.items() if len(v) == 1), None)
    if not multi or not single:
        pytest.skip('snapshot lacks a single- and a multi-occurrence subject')
    ctx = {'program': sc['programcode'], 'year_level': sc['yearlevel'], 'ay_id': sc['academicyearid'],
           'term': sc['semestertype'], 'section_id': sc['sectionid']}
    return dict(ctx=ctx, semesterid=sc['semesterid'], occurrences=occ, single=single, multi=multi)


def _sess(o, **change):
    s = {'subject_code': o['subjectcode'], 'day': o['daydesc'], 'starttimeid': o['starttimeid'],
         'endtimeid': o['endtimeid'], 'roomid': o['roomid'], 'room_id': o['roomid'],
         'official_sessionid': o['sessionid']}
    s.update(change)
    return s


def _free_move(client, ctx, o):
    """Same room and duration, first weekday slot the conflict check accepts."""
    span = o['endtimeid'] - o['starttimeid']
    for day in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'):
        for st in range(1, 29 - span):
            if (day, st) == (o['daydesc'], o['starttimeid']):
                continue
            cand = _sess(o, day=day, starttimeid=st, endtimeid=st + span)
            r = client.post('/api/local/check_room_conflicts', json=dict(ctx, sessions=[cand]))
            if r.status_code == 200 and not r.get_json().get('conflicts'):
                return cand
    pytest.skip(f"no conflict-free slot for {o['subjectcode']}")


def _effective(client, ctx):
    rows = client.get('/api/manual/section_schedule', query_string=dict(
        program=ctx['program'], year_level=str(ctx['year_level']), ay_id=ctx['ay_id'],
        semester=ctx['term'], section_id=str(ctx['section_id']), scheduler_mode='local')).get_json()
    return sorted((r.get('schedule_source') or 'Official', r.get('official_sessionid'),
                   r['daydesc'], r['starttimeid']) for r in rows)


def _official_rows(real, scope):
    return _q(real, """
        SELECT sv.versionid, sv.status, ss.sessionid, ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid
        FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid
        LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        WHERE s.sectionid = %s AND s.semesterid = %s ORDER BY 1, 3
    """, (scope['ctx']['section_id'], scope['semesterid']))


def _save(client, ctx, sessions, reason='lifecycle'):
    return client.post('/api/local/save_arrangement', json=dict(ctx, sessions=sessions, reason=reason))


def _publish(client, saved):
    return client.post(f"/api/local/arrangement/{saved['arrangement_id']}/publish",
                       json={'reason': 'lifecycle', 'expected_updated_at': saved['updated_at']})


def test_partial_override_lifecycle(env, scope):
    client, real = env
    ctx, single, multi = scope['ctx'], scope['single'], scope['multi']
    official_before = _official_rows(real, scope)
    baseline = _effective(client, ctx)
    assert {r[0] for r in baseline} == {'Official'} and len(baseline) == len(scope['occurrences'])

    # 1. One occurrence alone is a valid Draft; unrelated occurrences are not required.
    moved = _free_move(client, ctx, single)
    r = _save(client, ctx, [moved])
    saved = r.get_json()
    assert r.status_code == 200 and saved['status'] == 'Draft', saved
    stored = _q(real, 'SELECT official_sessionid, daydesc, starttimeid FROM local_arrangement_sessions '
                      'WHERE arrangementid = %s', (saved['arrangement_id'],))
    assert [(x['official_sessionid'], x['daydesc'], x['starttimeid']) for x in stored] == \
        [(single['sessionid'], moved['day'], moved['starttimeid'])]

    # 2. A Draft does not change the effective schedule.
    assert _effective(client, ctx) == baseline

    # 3. Publishing replaces exactly that occurrence.
    r = _publish(client, saved)
    assert r.status_code == 200, r.get_json()
    eff = _effective(client, ctx)
    assert len(eff) == len(baseline)
    assert ('Local', single['sessionid'], moved['day'], moved['starttimeid']) in eff
    assert not any(e[0] == 'Official' and e[1] == single['sessionid'] for e in eff)

    # 4. One meeting of a multi-occurrence subject; the other stays Official and the
    #    earlier Published override is carried forward into the new Draft.
    first, sibling = multi[0], multi[1]
    moved2 = _free_move(client, ctx, first)
    r = _save(client, ctx, [moved2])
    saved2 = r.get_json()
    assert r.status_code == 200, saved2
    ids2 = sorted(x['official_sessionid'] for x in _q(
        real, 'SELECT official_sessionid FROM local_arrangement_sessions WHERE arrangementid = %s',
        (saved2['arrangement_id'],)))
    assert ids2 == sorted([single['sessionid'], first['sessionid']])
    assert _publish(client, saved2).status_code == 200
    eff = _effective(client, ctx)
    local_ids = {e[1] for e in eff if e[0] != 'Official'}
    assert local_ids == {single['sessionid'], first['sessionid']}
    assert any(e[0] == 'Official' and e[1] == sibling['sessionid'] for e in eff)
    assert len(eff) == len(baseline)

    # 5. Archiving falls back to the original Official occurrences.
    upd = _q(real, 'SELECT updated_at FROM local_arrangement WHERE arrangementid = %s',
             (saved2['arrangement_id'],))[0]['updated_at'].isoformat()
    r = client.post(f"/api/local/arrangement/{saved2['arrangement_id']}/deactivate",
                    json={'expected_updated_at': upd})
    assert r.status_code == 200, r.get_json()
    assert _effective(client, ctx) == baseline

    # 6. The Official Published schedule itself never changed.
    assert _official_rows(real, scope) == official_before


def test_duplicate_occurrence_is_rejected(env, scope):
    client, _ = env
    o = scope['single']
    r = _save(client, scope['ctx'], [_sess(o, day='Monday'), _sess(o, day='Tuesday')])
    assert r.status_code == 409 and r.get_json()['code'] == 'DUPLICATE_OFFICIAL_OCCURRENCE'


def test_occurrence_from_another_section_is_rejected(env, scope):
    client, real = env
    foreign = _q(real, """
        SELECT ss.sessionid FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid AND sv.status = 'Published'
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        WHERE s.sectionid <> %s LIMIT 1
    """, (scope['ctx']['section_id'],))
    if not foreign:
        pytest.skip('no other section has a Published schedule')
    r = _save(client, scope['ctx'], [_sess(scope['single'], official_sessionid=foreign[0]['sessionid'])])
    assert r.status_code == 409 and r.get_json()['success'] is False


def test_nonexistent_occurrence_is_rejected(env, scope):
    client, _ = env
    r = _save(client, scope['ctx'], [_sess(scope['single'], official_sessionid=2_000_000_000)])
    assert r.status_code == 409 and r.get_json()['success'] is False


def test_conflict_check_includes_unchanged_official_occurrences(env, scope):
    client, _ = env
    single = scope['single']
    other = next(o for o in scope['occurrences'] if o['subjectcode'] != single['subjectcode'])
    # Move the single occurrence onto another subject's untouched Official slot.
    cand = _sess(single, day=other['daydesc'], starttimeid=other['starttimeid'],
                 endtimeid=other['starttimeid'] + (single['endtimeid'] - single['starttimeid']))
    body = client.post('/api/local/check_room_conflicts',
                       json=dict(scope['ctx'], sessions=[cand])).get_json()
    assert body['success'] is True
    assert any(c.get('official_sessionid') == other['sessionid'] and c['conflict_type'] == 'Section'
               for c in body['conflicts']), body['conflicts']
