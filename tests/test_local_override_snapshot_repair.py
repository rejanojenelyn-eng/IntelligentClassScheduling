"""Regression tests for the Local Scheduler repair (Phases 6-8).

Local is an override layer: local_arrangement + local_arrangement_sessions.official_sessionid.
  - Save creates an inactive Draft; Publish activates it. Neither touches Official tables.
  - A section has ONE active Local Published snapshot; Local Publish and approved Schedule
    Adjustments both build the next snapshot from the current one, so they never drop each
    other's overrides.
  - ref_versionid is snapshot/audit metadata chosen by one shared anchor; official_sessionid
    is the authoritative occurrence link.

DB-backed tests run real endpoints inside one rolled-back transaction (tests/_shared_tx.py).
"""
import datetime
import threading
import time

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db


# ── Phase 6: schema helpers never run concurrently; Local errors are logged ──────

def test_concurrent_first_requests_apply_local_schema_once(monkeypatch):
    calls = []

    def _slow_apply():
        calls.append(1)
        time.sleep(0.2)
        app_module._local_tables_ensured = True

    monkeypatch.setattr(app_module, '_local_tables_ensured', False)
    monkeypatch.setattr(app_module, '_apply_local_tables', _slow_apply)
    threads = [threading.Thread(target=app_module._ensure_local_tables) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(calls) == 1


def test_concurrent_first_requests_apply_schedule_version_schema_once(monkeypatch):
    calls = []

    def _slow_apply():
        calls.append(1)
        time.sleep(0.2)
        app_module._source_col_ensured = True
        app_module._original_status_col_ensured = True
        app_module._schedule_version_empnum_col_ensured = True
        return True

    for flag in ('_source_col_ensured', '_original_status_col_ensured',
                 '_schedule_version_empnum_col_ensured'):
        monkeypatch.setattr(app_module, flag, False)
    monkeypatch.setattr(app_module, '_apply_schedule_version_schema', _slow_apply)
    threads = [threading.Thread(target=app_module._ensure_schedule_version_schema) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(calls) == 1


def test_local_server_errors_are_logged_not_swallowed(caplog):
    with app_module.app.test_request_context('/api/local/save_arrangement', method='POST'):
        try:
            raise RuntimeError('boom-local')
        except RuntimeError:
            with caplog.at_level('ERROR'):
                resp, status = app_module._safe_local_api_error()
    assert status == 500
    assert 'boom-local' not in resp.get_data(as_text=True)          # generic for the client
    assert any('boom-local' in (r.exc_text or '') or 'Local Scheduler request failed' in r.getMessage()
               for r in caplog.records)


def test_merge_local_overrides_replaces_by_occurrence_and_carries_the_rest():
    current = [{'official_sessionid': 1, 'daydesc': 'Monday'},
               {'official_sessionid': 2, 'daydesc': 'Tuesday'}]
    new = [{'official_sessionid': 2, 'daydesc': 'Friday'}, {'official_sessionid': 3, 'daydesc': 'Saturday'}]
    merged = app_module._merge_local_overrides(current, new)
    assert sorted((m['official_sessionid'], m['daydesc']) for m in merged) == [
        (1, 'Monday'), (2, 'Friday'), (3, 'Saturday')]


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
def ls(tx):
    """A real section with a Published Official schedule (>= 3 occurrences) in the active
    semester. Any active Local state for it is archived inside the rolled-back transaction."""
    cur = _cur(tx)
    cur.execute("""
        SELECT s.sectionid, UPPER(c.programcode) AS program, cs.yearlevel, sem.semesterid,
               sem.academicyearid, sem.semestertype
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        JOIN curriculum c ON cs.curriculumid = c.curriculumid
        JOIN semester sem ON sem.semesterid = s.semesterid
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        WHERE sv.status = 'Published' AND sem.isactive
        GROUP BY 1,2,3,4,5,6
        HAVING COUNT(DISTINCT ss.sessionid) >= 3 AND COUNT(DISTINCT cs.subjectcode) >= 2
        ORDER BY 1 LIMIT 1
    """)
    sc = cur.fetchone()
    if not sc:
        pytest.skip('no section with a Published Official schedule in the active semester')
    cur.execute("""
        UPDATE local_arrangement SET status = 'Archived', is_active = FALSE,
               archive_reason = 'test isolation', archived_at = NOW()
        WHERE sectionid = %s AND semesterid = %s AND status IN ('Draft', 'Published')
    """, (sc['sectionid'], sc['semesterid']))
    cur.execute("""
        SELECT ss.sessionid, ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid, sv.versionid,
               sv.scheduleid, UPPER(cs.subjectcode) AS code,
               COALESCE(sv.employeenumber, s.employeenumber) AS fac
        FROM schedule_sessions ss
        JOIN schedule_version sv ON ss.versionid = sv.versionid
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN curriculumsubject cs ON s.curriculumsubjectid = cs.curriculumsubjectid
        WHERE sv.status = 'Published' AND s.sectionid = %s AND s.semesterid = %s
        ORDER BY ss.sessionid
    """, (sc['sectionid'], sc['semesterid']))
    occ = cur.fetchall()
    cur.execute("""SELECT a.username, a.employeenumber FROM accounts a
                   WHERE a.isactive AND a.role = 'Academic Head' AND a.employeenumber IS NOT NULL
                   ORDER BY a.userid LIMIT 1""")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Academic Head account with an employee number')
    tx.handle().commit()
    return dict(sc, occ=occ, username=acct['username'], empnum=acct['employeenumber'])


@pytest.fixture
def client(ls):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['loggedin'] = True
        s['role'] = 'Academic Head'
        s['username'] = ls['username']
        s['employeenumber'] = ls['empnum']
    return c


def _official_rows(tx, ls):
    cur = _cur(tx)
    cur.execute("""
        SELECT sv.versionid, sv.status, sv.version_number, ss.sessionid, ss.daydesc,
               ss.starttimeid, ss.endtimeid, ss.roomid
        FROM schedule_version sv
        JOIN schedule s ON sv.scheduleid = s.scheduleid
        JOIN schedule_sessions ss ON ss.versionid = sv.versionid
        WHERE s.sectionid = %s AND s.semesterid = %s ORDER BY ss.sessionid
    """, (ls['sectionid'], ls['semesterid']))
    return [tuple(r.values()) for r in cur.fetchall()]


def _active_local(tx, ls):
    cur = _cur(tx)
    cur.execute("""
        SELECT la.arrangementid, las.official_sessionid, las.daydesc
        FROM local_arrangement la JOIN local_arrangement_sessions las USING (arrangementid)
        WHERE la.sectionid = %s AND la.semesterid = %s AND la.status = 'Published' AND la.is_active
        ORDER BY las.official_sessionid
    """, (ls['sectionid'], ls['semesterid']))
    rows = cur.fetchall()
    return {r['arrangementid'] for r in rows}, sorted(r['official_sessionid'] for r in rows)


def _sess(o):
    return {'subject_code': o['code'], 'day': o['daydesc'], 'starttimeid': o['starttimeid'],
            'endtimeid': o['endtimeid'], 'roomid': o['roomid'],
            'official_sessionid': o['sessionid'], 'faculty_id': o['fac']}


def _save(client, ls, occs, reason='test'):
    r = client.post('/api/local/save_arrangement', json={
        'program': ls['program'], 'year_level': ls['yearlevel'], 'ay_id': ls['academicyearid'],
        'term': ls['semestertype'], 'section_id': ls['sectionid'], 'reason': reason,
        'sessions': [_sess(o) for o in occs]})
    body = r.get_json()
    assert r.status_code == 200 and body['success'], body
    return body


def _publish(client, saved):
    r = client.post(f"/api/local/arrangement/{saved['arrangement_id']}/publish",
                    json={'reason': 'test publish', 'expected_updated_at': saved.get('updated_at')})
    return r


@pytest.fixture
def clean_request_rules(monkeypatch):
    """Isolate the adjustment-approval persistence from scheduling-rule evaluation."""
    monkeypatch.setattr(app_module, '_request_conflict_summary',
                        lambda *a, **k: ({'all_ok': True, 'conflicts': []}, None))
    monkeypatch.setattr(app_module, '_validate_local_editable_dimensions', lambda *a, **k: (True, None))
    monkeypatch.setattr(app_module, '_validate_local_faculty_service_rules', lambda *a, **k: (True, None))


def _approve_adjustment(tx, client, ls, o, new_day='Saturday'):
    cur = _cur(tx)
    cur.execute("""
        INSERT INTO schedule_change_request
            (scheduleid, versionid, change_type, new_daydesc, effective_from, reason,
             submitted_by, status, official_sessionid)
        VALUES (%s, %s, 'Day', %s, %s, 'test adjustment', %s, 'Pending', %s)
        RETURNING requestid
    """, (o['scheduleid'], o['versionid'], new_day, datetime.date.today(), o['fac'], o['sessionid']))
    rid = cur.fetchone()['requestid']
    tx.handle().commit()
    r = client.post('/api/requests/decide', json={'type': 'adjustment', 'id': rid,
                                                  'decision': 'Approved', 'remarks': 'ok'})
    body = r.get_json()
    assert r.status_code == 200 and body['success'], body
    return rid


# ── Phase 6: Local lifecycle never touches Official ─────────────────────────────

@requires_db
def test_local_save_is_an_inactive_draft_and_official_is_untouched(tx, ls, client):
    before = _official_rows(tx, ls)
    saved = _save(client, ls, [ls['occ'][0]])
    cur = _cur(tx)
    cur.execute("SELECT status, is_active FROM local_arrangement WHERE arrangementid = %s",
                (saved['arrangement_id'],))
    assert dict(cur.fetchone()) == {'status': 'Draft', 'is_active': False}
    assert _official_rows(tx, ls) == before
    assert _active_local(tx, ls) == (set(), [])          # effective schedule unchanged


@requires_db
def test_local_publish_activates_the_override_and_official_is_untouched(tx, ls, client):
    before = _official_rows(tx, ls)
    saved = _save(client, ls, [ls['occ'][0]])
    r = _publish(client, saved)
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()
    assert _official_rows(tx, ls) == before
    assert _active_local(tx, ls) == ({saved['arrangement_id']}, [ls['occ'][0]['sessionid']])


# ── Phase 7: adjustments and Local publishes never knock each other out ─────────

@requires_db
def test_order_a_adjustment_then_local_publish_keeps_both(tx, ls, client, clean_request_rules):
    o1, o2 = ls['occ'][0], ls['occ'][1]
    # Same placement: the later Local publish re-validates the carried override, and this
    # test is about persistence, not about a (bypassed) conflict check.
    _approve_adjustment(tx, client, ls, o1, new_day=o1['daydesc'])
    assert _active_local(tx, ls)[1] == [o1['sessionid']]
    saved = _save(client, ls, [o2])
    r = _publish(client, saved)
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()
    ids, occs = _active_local(tx, ls)
    assert len(ids) == 1 and occs == sorted([o1['sessionid'], o2['sessionid']])


@requires_db
def test_order_a_prime_draft_saved_before_adjustment_still_keeps_it(tx, ls, client, clean_request_rules):
    """Draft saved first, adjustment approved, THEN the Draft is published."""
    o1, o2 = ls['occ'][0], ls['occ'][1]
    saved = _save(client, ls, [o2])
    _approve_adjustment(tx, client, ls, o1)
    r = _publish(client, saved)
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()
    assert _active_local(tx, ls)[1] == sorted([o1['sessionid'], o2['sessionid']])


@requires_db
def test_order_b_local_publish_then_adjustment_keeps_both(tx, ls, client, clean_request_rules):
    o1, o2 = ls['occ'][0], ls['occ'][1]
    saved = _save(client, ls, [o2])
    assert _publish(client, saved).get_json()['success']
    _approve_adjustment(tx, client, ls, o1)
    ids, occs = _active_local(tx, ls)
    assert len(ids) == 1 and saved['arrangement_id'] not in ids
    assert occs == sorted([o1['sessionid'], o2['sessionid']])
    cur = _cur(tx)
    cur.execute("SELECT status, archive_reason FROM local_arrangement WHERE arrangementid = %s",
                (saved['arrangement_id'],))
    assert dict(cur.fetchone()) == {'status': 'Archived',
                                    'archive_reason': 'Superseded by a newer approved Schedule Adjustment'}


@requires_db
def test_adjustment_replaces_an_existing_override_of_the_same_occurrence(tx, ls, client, clean_request_rules):
    o1 = ls['occ'][0]
    saved = _save(client, ls, [o1])
    assert _publish(client, saved).get_json()['success']
    rid = _approve_adjustment(tx, client, ls, o1, new_day='Saturday')
    cur = _cur(tx)
    cur.execute("""
        SELECT la.override_reason, las.daydesc FROM local_arrangement la
        JOIN local_arrangement_sessions las USING (arrangementid)
        WHERE la.sectionid = %s AND la.semesterid = %s AND la.status = 'Published' AND la.is_active
    """, (ls['sectionid'], ls['semesterid']))
    rows = cur.fetchall()
    assert [r['daydesc'] for r in rows] == ['Saturday']                      # one override, adjusted
    assert rows[0]['override_reason'] == f'Approved Schedule Adjustment request #{rid}'  # traceable


@requires_db
def test_adjustment_never_creates_an_approved_official_version(tx, ls, client, clean_request_rules):
    before = _official_rows(tx, ls)
    _approve_adjustment(tx, client, ls, ls['occ'][0])
    assert _official_rows(tx, ls) == before
    cur = _cur(tx)
    cur.execute("SELECT COUNT(*) AS n FROM schedule_version WHERE status NOT IN ('Draft','Published','Archive')")
    assert cur.fetchone()['n'] == 0


# ── Phase 8: one anchor; staleness is per snapshot ───────────────────────────────

@requires_db
def test_all_local_paths_share_one_anchor_definition():
    import inspect
    for fn in ('api_save_local_arrangement', 'api_publish_local_arrangement',
               'api_restore_local_arrangement', 'api_local_check_room_conflicts',
               'api_requests_decide'):
        assert '_current_official_anchor(' in inspect.getsource(getattr(app_module, fn)), fn


@requires_db
def test_anchor_from_another_subject_of_the_same_snapshot_is_not_stale(tx, ls, client):
    saved = _save(client, ls, [ls['occ'][0]])
    cur = _cur(tx)
    anchor = app_module._current_official_anchor(cur, ls['program'], ls['yearlevel'],
                                                 ls['sectionid'], ls['semesterid'])
    other = next(o['versionid'] for o in ls['occ'] if o['versionid'] != anchor['versionid'])
    cur.execute("UPDATE local_arrangement SET ref_versionid = %s WHERE arrangementid = %s",
                (other, saved['arrangement_id']))
    tx.handle().commit()
    r = _publish(client, saved)
    assert r.status_code == 200 and r.get_json()['success'], r.get_json()


@requires_db
def test_anchor_from_an_archived_snapshot_is_stale(tx, ls, client):
    saved = _save(client, ls, [ls['occ'][0]])
    cur = _cur(tx)
    # An older snapshot of this section (rolled back with the test transaction).
    cur.execute("""
        INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status)
        SELECT %s, COALESCE(MAX(version_number), 0) + 1, 'Archive', 'manual_editor', 'Published'
        FROM schedule_version WHERE scheduleid = %s
        RETURNING versionid
    """, (ls['occ'][0]['scheduleid'], ls['occ'][0]['scheduleid']))
    archived = cur.fetchone()
    cur.execute("UPDATE local_arrangement SET ref_versionid = %s WHERE arrangementid = %s",
                (archived['versionid'], saved['arrangement_id']))
    tx.handle().commit()
    r = _publish(client, saved)
    assert r.status_code == 409 and r.get_json()['code'] == 'STALE_OFFICIAL_SCHEDULE'
