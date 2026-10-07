"""Regression tests for the Official schedule persistence repair (Phases 1-4).

Architecture under test:
  schedule -> schedule_version -> schedule_sessions; statuses Draft / Published / Archive.
  Save Draft never touches Published. Only a successful Publish replaces Published, and
  publishing subject A never publishes subject B's pending Draft.

DB-backed tests run the real endpoints inside one rolled-back transaction
(tests/_shared_tx.py); nothing they write persists.
"""
import concurrent.futures
import inspect
import json

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db


# ── Phase 1: no schema DDL inside request transactions ──────────────────────────

class _RecordingCursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)


def test_legacy_ensure_helpers_never_execute_on_the_request_cursor(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, '_ensure_schedule_version_schema', lambda: calls.append(1) or True)
    cur = _RecordingCursor()
    app_module._ensure_source_col(cur)
    app_module._ensure_original_status_col(cur)
    app_module._ensure_schedule_version_empnum_col(cur)
    assert cur.sql == [], 'schema helpers must never run SQL on a caller (request) cursor'
    assert len(calls) == 3


@pytest.mark.parametrize('fn', ['api_approve_schedule', 'api_save_draft', '_insert_batch',
                                'api_restore_version'])
def test_persistence_paths_contain_no_schema_ddl(fn):
    src = inspect.getsource(getattr(app_module, fn))
    for banned in ('ALTER TABLE', '_ensure_source_col(', '_ensure_original_status_col(',
                   '_ensure_schedule_version_empnum_col(', '_ensure_local_tables('):
        assert banned not in src, f'{fn} must not run schema migration ({banned})'


def test_schema_helpers_run_at_startup_and_before_views():
    assert '_ensure_runtime_schema()' in inspect.getsource(app_module._run_startup_migrations)
    hook = inspect.getsource(app_module._ensure_runtime_schema_before_views)
    assert '_ensure_runtime_schema()' in hook


# ── Shared DB fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def tx(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    try:
        yield t
    finally:
        t.close()


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


@pytest.fixture
def scope(tx):
    """A real section in the active semester with no schedule rows yet, two of its
    curriculum subjects, two lecture rooms and one faculty member."""
    cur = _cur(tx)
    cur.execute("""
        SELECT sec.sectionid, UPPER(pyl.programcode) AS program, pyl.yearlevel,
               sem.semesterid, sem.academicyearid, sem.semestertype
        FROM sections sec
        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        JOIN semester sem ON sem.academicyearid = pyl.academicyearid
        WHERE sem.isactive AND sec.isactive
          AND NOT EXISTS (SELECT 1 FROM schedule s
                          WHERE s.sectionid = sec.sectionid AND s.semesterid = sem.semesterid)
          AND (SELECT COUNT(DISTINCT cs.subjectcode) FROM curriculumsubject cs
               JOIN curriculum c ON cs.curriculumid = c.curriculumid
               WHERE c.programcode = pyl.programcode AND cs.yearlevel = pyl.yearlevel
                 AND cs.semester = sem.semestertype) >= 2
        ORDER BY sec.sectionid LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        pytest.skip('no empty section with curriculum subjects in the active semester')
    cur.execute("""
        SELECT DISTINCT UPPER(cs.subjectcode) AS code
        FROM curriculumsubject cs JOIN curriculum c ON cs.curriculumid = c.curriculumid
        WHERE UPPER(c.programcode) = %s AND cs.yearlevel = %s AND cs.semester = %s
        ORDER BY 1 LIMIT 2
    """, (row['program'], row['yearlevel'], row['semestertype']))
    codes = [r['code'] for r in cur.fetchall()]
    cur.execute("SELECT roomid FROM room WHERE isactive AND roomtype = 'Lecture' ORDER BY roomid LIMIT 2")
    rooms = [r['roomid'] for r in cur.fetchall()]
    cur.execute("SELECT employeenumber FROM faculty WHERE isactive ORDER BY employeenumber LIMIT 1")
    fac = cur.fetchone()['employeenumber']
    cur.execute("SELECT username FROM accounts WHERE isactive ORDER BY userid LIMIT 1")
    user = cur.fetchone()['username']
    return dict(row, A=codes[0], B=codes[1], rooms=rooms, faculty=fac, username=user)


def _row(scope, code, day, start, end, room_idx=0, **extra):
    r = {
        'subjectcode': code, 'subject_code': code,
        'daydesc': day, 'day': day, 'days_list': [day],
        'start_time': start, 'end_time': end,
        'roomid': scope['rooms'][room_idx], 'room_id': scope['rooms'][room_idx],
        'employeenumber': scope['faculty'], 'faculty_id': scope['faculty'],
        'sem': scope['semestertype'],
    }
    r.update(extra)
    return r


def _seed(tx, scope, status, version, rows, source='manual_editor'):
    cur = _cur(tx)
    app_module._insert_batch(cur, rows, scope['semesterid'], status, version,
                             scope['program'], scope['yearlevel'],
                             source=source, section_id=scope['sectionid'])
    tx.handle().commit()


def _state(tx, scope):
    """{(code, status): sorted [(day, 'HH:MM')]} plus versionids for the section."""
    cur = _cur(tx)
    cur.execute("""
        SELECT UPPER(cs.subjectcode) AS code, sv.status, sv.versionid, sv.version_number,
               ss.sessionid, ss.daydesc, LEFT(t.timevalue::text, 5) AS start
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        JOIN timeslot t ON ss.starttimeid = t.timeid
        WHERE s.sectionid = %s AND s.semesterid = %s AND sv.status IN ('Draft', 'Published')
    """, (scope['sectionid'], scope['semesterid']))
    out = {}
    for r in cur.fetchall():
        out.setdefault((r['code'], r['status']), []).append((r['daydesc'], r['start']))
    return {k: sorted(v) for k, v in out.items()}


def _versions(tx, scope, code, status):
    cur = _cur(tx)
    cur.execute("""
        SELECT sv.versionid, sv.version_number, sv.is_incomplete
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE s.sectionid = %s AND s.semesterid = %s AND UPPER(cs.subjectcode) = %s
          AND sv.status = %s ORDER BY sv.versionid
    """, (scope['sectionid'], scope['semesterid'], code, status))
    return cur.fetchall()


def _session_ids(tx, versionid):
    cur = _cur(tx)
    cur.execute("SELECT sessionid, daydesc FROM schedule_sessions WHERE versionid = %s ORDER BY sessionid",
                (versionid,))
    return cur.fetchall()


@pytest.fixture
def client(scope):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['loggedin'] = True
        s['role'] = 'Academic Head'
        s['username'] = scope['username']
    return c


class _NoViolations:
    violations = []


class _CleanConstraintService:
    def __init__(self, *a, **k):
        pass

    @classmethod
    def with_current_policy(cls):
        return cls()

    def validate_schedule(self, *a, **k):
        return _NoViolations()


@pytest.fixture
def clean_gates(monkeypatch):
    """Isolate persistence from scheduling rules: every publish gate reports clean."""
    monkeypatch.setattr(app_module, 'ConstraintService', _CleanConstraintService)
    monkeypatch.setattr(app_module, '_unresolved_components', lambda cls, refs: (set(), None))
    monkeypatch.setattr(app_module, '_check_cross_program_faculty_loads', lambda *a, **k: [])
    monkeypatch.setattr(app_module, '_check_designee_night_limit', lambda *a, **k: [])
    monkeypatch.setattr(app_module, '_publish_carried_forward_conflicts', lambda *a, **k: [])
    monkeypatch.setattr(app_module, '_check_cross_schedule_conflicts', lambda *a, **k: ([], 0))


def _ctx(scope):
    return {'program': scope['program'], 'yearLevel': scope['yearlevel'],
            'term': scope['semestertype'], 'acadYear': scope['academicyearid'],
            'sectionId': scope['sectionid'], 'section_id': scope['sectionid']}


def _approve(client, scope, rows, **extra):
    payload = {'schedule_data': rows, 'context': _ctx(scope), 'override': True}
    payload.update(extra)
    return client.post('/api/schedule/approve', data=json.dumps(payload, default=str),
                       content_type='application/json')


# ── Phase 1: Approve completes instead of self-blocking ─────────────────────────

@requires_db
def test_approve_never_holds_a_schema_lock_that_its_own_second_connection_waits_on(monkeypatch):
    """Real connections (no shared transaction). Reset the 'schema ensured' flags so the
    old code would ALTER TABLE inside the Approve transaction, then probe schedule_version
    from a separate connection at the exact point Approve opens its second connection.
    The probe aborts Approve before any write, so nothing is persisted."""
    import psycopg2
    from config import Config

    conn = psycopg2.connect(dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
                            host=Config.DB_HOST, port=Config.DB_PORT, connect_timeout=5)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT sec.sectionid, UPPER(pyl.programcode) AS program, pyl.yearlevel,
               sem.academicyearid, sem.semestertype
        FROM sections sec
        JOIN program_yearlevel pyl ON sec.programyearlevelid = pyl.programyearlevelid
        JOIN semester sem ON sem.academicyearid = pyl.academicyearid
        WHERE sem.isactive AND sec.isactive ORDER BY sec.sectionid LIMIT 1
    """)
    sc = cur.fetchone()
    cur.execute("SELECT username FROM accounts WHERE isactive ORDER BY userid LIMIT 1")
    user = cur.fetchone()['username']
    conn.close()
    if not sc:
        pytest.skip('no active section')

    for flag in ('_source_col_ensured', '_original_status_col_ensured',
                 '_schedule_version_empnum_col_ensured'):
        monkeypatch.setattr(app_module, flag, False)
    monkeypatch.setattr(app_module, 'ConstraintService', _CleanConstraintService)
    monkeypatch.setattr(app_module, '_unresolved_components', lambda cls, refs: (set(), None))
    monkeypatch.setattr(app_module, '_check_cross_program_faculty_loads', lambda *a, **k: [])
    monkeypatch.setattr(app_module, '_check_designee_night_limit', lambda *a, **k: [])
    monkeypatch.setattr(app_module, '_publish_carried_forward_conflicts', lambda *a, **k: [])

    probe = {}

    class _Abort(Exception):
        pass

    def _probe(*a, **k):
        other = psycopg2.connect(dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
                                 host=Config.DB_HOST, port=Config.DB_PORT, connect_timeout=5)
        try:
            c2 = other.cursor()
            c2.execute("SET lock_timeout = '2s'")
            c2.execute("SELECT COUNT(*) FROM public.schedule_version")
            probe['ok'] = True
        except Exception as e:
            probe['error'] = str(e)
        finally:
            other.rollback(); other.close()
        raise _Abort('stop before any write')

    monkeypatch.setattr(app_module, '_check_cross_schedule_conflicts', _probe)

    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['loggedin'] = True; s['role'] = 'Academic Head'; s['username'] = user
    payload = {'override': True, 'context': {
        'program': sc['program'], 'yearLevel': sc['yearlevel'], 'term': sc['semestertype'],
        'acadYear': sc['academicyearid'], 'sectionId': sc['sectionid']},
        'schedule_data': [{'subject_code': 'ZZZ-PROBE', 'day': 'Monday', 'days_list': ['Monday'],
                           'start_time': '07:30', 'end_time': '09:00'}]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: c.post('/api/schedule/approve', data=json.dumps(payload),
                                       content_type='application/json'))
        resp = fut.result(timeout=60)   # the old code hung here forever

    assert probe.get('ok'), f"schedule_version was locked by Approve's own transaction: {probe}"
    assert resp.status_code == 500   # aborted by the probe, after the lock check


# ── Phase 2: Draft/Published boundaries ─────────────────────────────────────────

@requires_db
def test_generation_replace_archives_draft_only(tx, scope, client):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00')])
    _seed(tx, scope, 'Draft', 2, [_row(scope, A, 'Tuesday', '07:30', '09:00')])

    r = client.post('/api/schedule/archive-draft-for-editor', json={
        'program': scope['program'], 'yearLevel': scope['yearlevel'],
        'term': scope['semestertype'], 'acadYear': scope['academicyearid'],
        'section': scope['sectionid']})
    assert r.get_json()['success']
    st = _state(tx, scope)
    assert st[(A, 'Published')] == [('Monday', '07:30')]
    assert (A, 'Draft') not in st


@requires_db
def test_generation_replace_without_section_archives_nothing(tx, scope, client):
    A = scope['A']
    _seed(tx, scope, 'Draft', 1, [_row(scope, A, 'Tuesday', '07:30', '09:00')])
    r = client.post('/api/schedule/archive-draft-for-editor', json={
        'program': scope['program'], 'yearLevel': scope['yearlevel'],
        'term': scope['semestertype'], 'acadYear': scope['academicyearid']})
    assert r.get_json() == {'success': True, 'archived': 0}
    assert _state(tx, scope)[(A, 'Draft')] == [('Tuesday', '07:30')]


@requires_db
def test_generate_then_save_draft_keeps_published_live(tx, scope, client):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00')])
    client.post('/api/schedule/archive-draft-for-editor', json={
        'program': scope['program'], 'yearLevel': scope['yearlevel'],
        'term': scope['semestertype'], 'acadYear': scope['academicyearid'],
        'section': scope['sectionid']})
    r = client.post('/api/schedule/save-draft', data=json.dumps({
        'schedule_data': [_row(scope, A, 'Wednesday', '10:00', '11:30')],
        'context': {'program': scope['program'], 'yearLevel': scope['yearlevel'],
                    'term': scope['semestertype'], 'acadYear': scope['academicyearid'],
                    'section': scope['sectionid']},
    }), content_type='application/json')
    assert r.get_json()['success'], r.get_json()
    st = _state(tx, scope)
    assert st[(A, 'Published')] == [('Monday', '07:30')]
    assert st[(A, 'Draft')] == [('Wednesday', '10:00')]


@requires_db
def test_save_draft_requires_a_section(tx, scope, client):
    r = client.post('/api/schedule/save-draft', data=json.dumps({
        'schedule_data': [_row(scope, scope['A'], 'Monday', '07:30', '09:00')],
        'context': {'program': scope['program'], 'yearLevel': scope['yearlevel'],
                    'term': scope['semestertype'], 'acadYear': scope['academicyearid']},
    }), content_type='application/json')
    assert r.status_code == 409 and r.get_json()['code'] == 'SECTION_REQUIRED'
    assert _state(tx, scope) == {}


@requires_db
@pytest.mark.parametrize('mode', ['draft', 'replace', None])
def test_restore_creates_draft_and_keeps_published(tx, scope, client, mode):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00')])
    old_pub = _versions(tx, scope, A, 'Published')[0]['versionid']
    # Make v1 history: archive it and publish v2.
    _cur(tx).execute("UPDATE schedule_version SET status = 'Archive' WHERE versionid = %s", (old_pub,))
    _seed(tx, scope, 'Published', 2, [_row(scope, A, 'Friday', '13:00', '14:30')])
    _seed(tx, scope, 'Draft', 3, [_row(scope, A, 'Tuesday', '07:30', '09:00')])

    body = {} if mode is None else {'restore_mode': mode}
    r = client.post(f'/api/schedule/versions/{old_pub}/restore', json=body)
    data = r.get_json()
    assert data['success'] and data['restore_as'] == 'Draft', data
    st = _state(tx, scope)
    assert st[(A, 'Published')] == [('Friday', '13:00')]      # live schedule untouched
    assert st[(A, 'Draft')] == [('Monday', '07:30')]          # restored revision
    assert len(_versions(tx, scope, A, 'Draft')) == 1         # previous Draft archived


@requires_db
def test_delete_session_rejects_published_and_allows_draft(tx, scope, client):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, A, 'Thursday', '07:30', '09:00')])
    pub = _versions(tx, scope, A, 'Published')[0]['versionid']
    pub_sess = _session_ids(tx, pub)[0]['sessionid']

    r = client.post('/api/schedule/delete_session', json={'version_id': pub, 'session_id': pub_sess})
    assert r.status_code == 409 and r.get_json()['code'] == 'PUBLISHED_IMMUTABLE'
    r = client.post('/api/schedule/delete_session', json={'version_id': pub})
    assert r.status_code == 409
    assert _state(tx, scope)[(A, 'Published')] == [('Monday', '07:30'), ('Thursday', '07:30')]

    _seed(tx, scope, 'Draft', 2, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                  _row(scope, A, 'Thursday', '07:30', '09:00')])
    drf = _versions(tx, scope, A, 'Draft')[0]['versionid']
    drf_sess = [s for s in _session_ids(tx, drf) if s['daydesc'] == 'Thursday'][0]['sessionid']
    r = client.post('/api/schedule/delete_session', json={'version_id': drf, 'session_id': drf_sess})
    assert r.get_json()['success']
    st = _state(tx, scope)
    assert st[(A, 'Draft')] == [('Monday', '07:30')]
    assert st[(A, 'Published')] == [('Monday', '07:30'), ('Thursday', '07:30')]


# ── Phase 3: Publish carries untouched subjects' PUBLISHED content ──────────────

@requires_db
def test_publishing_a_does_not_publish_b_pending_draft(tx, scope, client, clean_gates):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)])
    _seed(tx, scope, 'Draft', 2, [_row(scope, A, 'Wednesday', '10:00', '11:30'),
                                  _row(scope, B, 'Friday', '10:00', '11:30', room_idx=1)])
    b_draft_before = _versions(tx, scope, B, 'Draft')[0]['versionid']

    r = _approve(client, scope, [_row(scope, A, 'Wednesday', '10:00', '11:30')])
    assert r.get_json()['success'], r.get_json()

    st = _state(tx, scope)
    assert st[(A, 'Published')] == [('Wednesday', '10:00')]   # A's intended result
    assert (A, 'Draft') not in st                              # A's Draft consumed
    assert st[(B, 'Published')] == [('Tuesday', '07:30')]     # B Published unchanged
    assert st[(B, 'Draft')] == [('Friday', '10:00')]          # B Draft still pending
    assert _versions(tx, scope, B, 'Draft')[0]['versionid'] == b_draft_before


@requires_db
def test_publish_all_publishes_every_submitted_draft(tx, scope, client, clean_gates):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)])
    _seed(tx, scope, 'Draft', 2, [_row(scope, A, 'Wednesday', '10:00', '11:30'),
                                  _row(scope, B, 'Friday', '10:00', '11:30', room_idx=1)])
    r = _approve(client, scope, [_row(scope, A, 'Wednesday', '10:00', '11:30'),
                                 _row(scope, B, 'Friday', '10:00', '11:30', room_idx=1)])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Wednesday', '10:00')],
                                 (B, 'Published'): [('Friday', '10:00')]}


@requires_db
def test_unselected_slices_of_the_published_subject_stay_draft(tx, scope, client, clean_gates):
    A = scope['A']
    _seed(tx, scope, 'Draft', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                  _row(scope, A, 'Thursday', '07:30', '09:00')])
    draft = _versions(tx, scope, A, 'Draft')[0]
    mon = [s for s in _session_ids(tx, draft['versionid']) if s['daydesc'] == 'Monday'][0]
    r = _approve(client, scope, [_row(scope, A, 'Monday', '07:30', '09:00',
                                      versionid=draft['versionid'], sessionid=mon['sessionid'])])
    assert r.get_json()['success'], r.get_json()
    st = _state(tx, scope)
    assert st[(A, 'Published')] == [('Monday', '07:30')]
    assert st[(A, 'Draft')] == [('Thursday', '07:30')]


@requires_db
def test_edited_draft_slice_is_consumed_by_sessionid(tx, scope, client, clean_gates):
    """Saturday slice moved to Tuesday then published: the old Saturday Draft row must not linger."""
    A = scope['A']
    _seed(tx, scope, 'Draft', 1, [_row(scope, A, 'Saturday', '07:30', '09:00')])
    draft = _versions(tx, scope, A, 'Draft')[0]
    sat = _session_ids(tx, draft['versionid'])[0]
    r = _approve(client, scope, [_row(scope, A, 'Tuesday', '07:30', '09:00',
                                      versionid=draft['versionid'], sessionid=sat['sessionid'])])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Tuesday', '07:30')]}


@requires_db
def test_other_subjects_incomplete_draft_does_not_block_publish(tx, scope, client, clean_gates):
    A, B = scope['A'], scope['B']
    cur = _cur(tx)
    app_module._insert_batch(cur, [_row(scope, B, 'Friday', '10:00', '11:30', room_idx=1)],
                             scope['semesterid'], 'Draft', 1, scope['program'], scope['yearlevel'],
                             section_id=scope['sectionid'], incomplete_map={B: ['room']})
    tx.handle().commit()
    r = _approve(client, scope, [_row(scope, A, 'Monday', '07:30', '09:00')])
    assert r.get_json()['success'], r.get_json()
    b = _versions(tx, scope, B, 'Draft')
    assert len(b) == 1 and b[0]['is_incomplete'] is True     # untouched, flag kept


@requires_db
def test_published_subject_can_be_removed_only_through_publish(tx, scope, client, clean_gates):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)])
    r = _approve(client, scope, [], removed_subjects=[B])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Monday', '07:30')]}

    r = _approve(client, scope, [], removed_subjects=['NOT-A-SUBJECT'])
    assert r.status_code == 409 and r.get_json()['code'] == 'REMOVED_SUBJECT_NOT_PUBLISHED'


@requires_db
def test_failed_publish_leaves_published_and_drafts_intact(tx, scope, client, clean_gates, monkeypatch):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)])
    _seed(tx, scope, 'Draft', 2, [_row(scope, A, 'Wednesday', '10:00', '11:30')])
    before = _state(tx, scope)

    def _boom(*a, **k):
        raise RuntimeError('simulated failure after archiving')
    monkeypatch.setattr(app_module, '_sync_mergedclass_for_semester', _boom)
    r = _approve(client, scope, [_row(scope, A, 'Wednesday', '10:00', '11:30')])
    assert r.status_code == 500
    assert _state(tx, scope) == before


@requires_db
def test_approve_requires_section_before_any_work(tx, scope, client, clean_gates):
    ctx = _ctx(scope)
    ctx.pop('sectionId'); ctx.pop('section_id')
    r = client.post('/api/schedule/approve', data=json.dumps({
        'schedule_data': [_row(scope, scope['A'], 'Monday', '07:30', '09:00')],
        'context': ctx, 'override': True}, default=str), content_type='application/json')
    assert r.status_code == 409 and r.get_json()['code'] == 'OFFICIAL_SECTION_REQUIRED'


@requires_db
def test_draft_sessions_is_section_scoped(tx, scope, client):
    A = scope['A']
    _seed(tx, scope, 'Draft', 1, [_row(scope, A, 'Monday', '07:30', '09:00')])
    q = (f"/api/schedule/draft_sessions?program={scope['program']}&year_level={scope['yearlevel']}"
         f"&ay_id={scope['academicyearid']}&sem={scope['semestertype']}")
    mine = client.get(q + f"&section_id={scope['sectionid']}").get_json()['sessions']
    assert [s['subject_code'].upper() for s in mine] == [A]
    assert all(s['versionid'] and s['sessionid'] and s['section_id'] == scope['sectionid'] for s in mine)
    other_section = scope['sectionid'] + 1000000
    other = client.get(q + f"&section_id={other_section}").get_json()['sessions']
    assert other == []


# ── Phase 4: version numbers across all sources ─────────────────────────────────

@requires_db
def test_save_draft_after_import_v1_uses_next_number_across_sources(tx, scope, client):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00')], source='import')
    # The counter is a program/year-level batch stamp, so sibling sections may already be
    # past 1; what matters is that it is computed across every source (never 1 again).
    expected = app_module._next_version_number(_cur(tx), scope['program'], scope['yearlevel'],
                                               scope['semesterid'], scope['sectionid'])
    assert expected >= 2
    r = client.post('/api/schedule/save-draft', data=json.dumps({
        'schedule_data': [_row(scope, A, 'Tuesday', '07:30', '09:00')],
        'context': {'program': scope['program'], 'yearLevel': scope['yearlevel'],
                    'term': scope['semestertype'], 'acadYear': scope['academicyearid'],
                    'section_id': scope['sectionid']},
    }), content_type='application/json')
    data = r.get_json()
    assert data['success'], data
    assert data['draft_version'] == expected
    st = _state(tx, scope)
    assert st[(A, 'Published')] == [('Monday', '07:30')]
    assert st[(A, 'Draft')] == [('Tuesday', '07:30')]


@requires_db
def test_restore_after_import_v1_does_not_collide(tx, scope, client):
    A = scope['A']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00')], source='import')
    v1 = _versions(tx, scope, A, 'Published')[0]['versionid']
    expected = app_module._next_version_number(_cur(tx), scope['program'], scope['yearlevel'],
                                               scope['semesterid'], scope['sectionid'])
    r = client.post(f'/api/schedule/versions/{v1}/restore', json={'restore_mode': 'draft'})
    data = r.get_json()
    assert data['success'] and data['new_version'] == expected >= 2, data
    assert _state(tx, scope)[(A, 'Published')] == [('Monday', '07:30')]


@requires_db
def test_next_version_number_counts_every_source(tx, scope):
    A = scope['A']
    cur = _cur(tx)
    before = app_module._next_version_number(cur, scope['program'], scope['yearlevel'],
                                             scope['semesterid'], scope['sectionid'])
    _seed(tx, scope, 'Published', before + 3, [_row(scope, A, 'Monday', '07:30', '09:00')],
          source='import')
    assert app_module._next_version_number(cur, scope['program'], scope['yearlevel'],
                                           scope['semesterid'], scope['sectionid']) == before + 4


# ── Pre-Phase 9: removing a subject is a Draft, published only explicitly ───────

def _save_draft(client, scope, rows, **extra):
    body = {'schedule_data': rows,
            'context': {'program': scope['program'], 'yearLevel': scope['yearlevel'],
                        'term': scope['semestertype'], 'acadYear': scope['academicyearid'],
                        'section_id': scope['sectionid']}}
    body.update(extra)
    return client.post('/api/schedule/save-draft', data=json.dumps(body, default=str),
                       content_type='application/json')


def _removal_markers(tx, scope, code):
    cur = _cur(tx)
    cur.execute("""
        SELECT sv.versionid, sv.status,
               (SELECT COUNT(*) FROM schedule_sessions ss WHERE ss.versionid = sv.versionid) AS n
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE s.sectionid = %s AND s.semesterid = %s AND UPPER(cs.subjectcode) = %s
          AND sv.is_removal = TRUE ORDER BY sv.versionid
    """, (scope['sectionid'], scope['semesterid'], code))
    return [(r['status'], r['n']) for r in cur.fetchall()]


def _seed_two_published(tx, scope):
    A, B = scope['A'], scope['B']
    _seed(tx, scope, 'Published', 1, [_row(scope, A, 'Monday', '07:30', '09:00'),
                                      _row(scope, B, 'Tuesday', '07:30', '09:00', room_idx=1)])
    return A, B


@requires_db
def test_saving_a_subject_removal_keeps_published_live(tx, scope, client):
    A, B = _seed_two_published(tx, scope)
    r = _save_draft(client, scope, [], removed_subjects=[B])
    assert r.get_json()['success'] and r.get_json()['removed_subjects'] == [B], r.get_json()
    st = _state(tx, scope)
    assert st[(B, 'Published')] == [('Tuesday', '07:30')]          # still live
    assert st[(A, 'Published')] == [('Monday', '07:30')]
    assert _removal_markers(tx, scope, B) == [('Draft', 0)]         # explicit, session-less

    q = (f"/api/manual/existing_sessions?subject_code={B}&ay_id={scope['academicyearid']}"
         f"&semester={scope['semestertype']}&section_id={scope['sectionid']}&scheduler_mode=official")
    assert client.get(q).get_json() == {'success': True, 'sessions': [], 'removed_in_draft': True}
    q = (f"/api/schedule/draft_sessions?program={scope['program']}&year_level={scope['yearlevel']}"
         f"&ay_id={scope['academicyearid']}&sem={scope['semestertype']}&section_id={scope['sectionid']}")
    assert client.get(q).get_json()['removed_subjects'] == [B]


@requires_db
def test_removal_draft_survives_saving_another_subject(tx, scope, client):
    A, B = _seed_two_published(tx, scope)
    assert _save_draft(client, scope, [], removed_subjects=[B]).get_json()['success']
    assert _save_draft(client, scope, [_row(scope, A, 'Wednesday', '10:00', '11:30')]).get_json()['success']
    assert [m for m in _removal_markers(tx, scope, B) if m[0] == 'Draft'] == [('Draft', 0)]
    st = _state(tx, scope)
    assert st[(A, 'Draft')] == [('Wednesday', '10:00')]
    assert st[(B, 'Published')] == [('Tuesday', '07:30')]


@requires_db
def test_publishing_another_subject_does_not_publish_a_pending_removal(tx, scope, client, clean_gates):
    A, B = _seed_two_published(tx, scope)
    assert _save_draft(client, scope, [], removed_subjects=[B]).get_json()['success']
    r = _approve(client, scope, [_row(scope, A, 'Wednesday', '10:00', '11:30')])
    assert r.get_json()['success'], r.get_json()
    st = _state(tx, scope)
    assert st[(B, 'Published')] == [('Tuesday', '07:30')]
    assert ('Draft', 0) in _removal_markers(tx, scope, B)            # still pending


@requires_db
def test_explicit_publish_of_the_removal_archives_published_and_the_marker(tx, scope, client, clean_gates):
    A, B = _seed_two_published(tx, scope)
    old_b = _versions(tx, scope, B, 'Published')[0]['versionid']
    assert _save_draft(client, scope, [], removed_subjects=[B]).get_json()['success']
    r = _approve(client, scope, [], removed_subjects=[B])
    assert r.get_json()['success'], r.get_json()
    assert _state(tx, scope) == {(A, 'Published'): [('Monday', '07:30')]}
    assert _removal_markers(tx, scope, B) == [('Archive', 0)]
    cur = _cur(tx)
    cur.execute("SELECT status FROM schedule_version WHERE versionid = %s", (old_b,))
    assert cur.fetchone()['status'] == 'Archive'


@requires_db
def test_removal_of_a_subject_that_is_not_published_is_rejected(tx, scope, client):
    _seed(tx, scope, 'Draft', 1, [_row(scope, scope['A'], 'Monday', '07:30', '09:00')])
    r = _save_draft(client, scope, [], removed_subjects=[scope['B']])
    assert r.status_code == 409 and r.get_json()['code'] == 'REMOVED_SUBJECT_NOT_PUBLISHED'
