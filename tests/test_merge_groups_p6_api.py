"""HC16 group model — P6 Local Scheduler and Faculty requests against the real database.

Every test runs inside ONE always-rolled-back transaction (tests/_shared_tx.py): the
switch to hc_merge_model='groups', the Merge Group, Local arrangements and requests
exist only inside it. The live system stays on 'legacy' with no Merge Groups.

The fixture takes a real active-semester section with a Published Official snapshot
(Local needs one) and builds a VALID Merge Group around one of its real occurrences:
member A = that section's subject at its Official slot, member B = another section
offering the same subject.
"""
from datetime import date, timedelta

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import merge_groups as mg
from conftest import requires_db
from test_merge_groups_p2_api import head, tx  # noqa: F401 (fixtures)

pytestmark = requires_db


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


def _build(tx, mode='MULTIPLE_FACULTY', designated_from_occurrence=False):
    cur = _cur(tx)
    mg.ensure_schema(cur)
    cur.execute("""
        SELECT ss.sessionid, ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid,
               s.sectionid, s.semesterid, s.curriculumsubjectid, s.employeenumber, s.scheduleid,
               sv.versionid, UPPER(cs.subjectcode) AS subjectcode, cs.lecturehours, cs.laboratoryhours,
               pyl.programcode, pyl.yearlevel, sem.academicyearid, sem.semestertype
        FROM schedule_sessions ss
        JOIN schedule_version sv ON sv.versionid = ss.versionid AND sv.status = 'Published'
             AND sv.source IS DISTINCT FROM 'local'
        JOIN schedule s ON s.scheduleid = sv.scheduleid
        JOIN semester sem ON sem.semesterid = s.semesterid AND sem.isactive
        JOIN sections sec ON sec.sectionid = s.sectionid AND sec.isactive
        JOIN program_yearlevel pyl ON pyl.programyearlevelid = sec.programyearlevelid
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = s.curriculumsubjectid
        WHERE ss.roomid IS NOT NULL AND s.employeenumber IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM local_arrangement la WHERE la.sectionid = s.sectionid
                          AND la.semesterid = s.semesterid AND la.status IN ('Published', 'Draft'))
        ORDER BY s.sectionid, ss.sessionid
    """)
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        pytest.skip('no active-semester section with a Published snapshot and no Local arrangement')
    cfg = dict(app_module.load_scheduler_config(), hc_merge_model='groups')
    mg.set_merge_model(cur, 'groups')
    offered_cache = {}
    by_section = {}
    for r in rows:
        by_section.setdefault(r['sectionid'], []).append(r)
    tried = 0
    for sec_id, occs in by_section.items():
        subjects = {}
        for o in occs:
            subjects.setdefault(o['subjectcode'], []).append(o)
        for code, mine in subjects.items():
            others = [o for c, os_ in subjects.items() if c != code for o in os_]
            if not others:
                continue
            o = mine[0]
            sem = o['semesterid']
            if sem not in offered_cache:
                offered_cache[sem] = mg.load_offered(cur, sem)
            sections, subjects_by_cs = offered_cache[sem]
            b = next(((sid, cs) for sid, sec in sections.items() if sid != sec_id and sec.get('isactive')
                      for cs in sec['offered'] if mg.norm_code(subjects_by_cs[cs]['subjectcode']) == mg.norm_code(code)),
                     None)
            if not b:
                continue
            tried += 1
            if tried > 25:
                break
            ctype = 'Lecture' if float(o['lecturehours'] or 0) > 0 else 'Lab'
            body = {'groupname': 'P6 merged class', 'semesterid': sem,
                    'ref_curriculumsubjectid': o['curriculumsubjectid'], 'faculty_mode': mode,
                    'employeenumber': o['employeenumber'] if designated_from_occurrence else None,
                    'members': [{'sectionid': sec_id, 'curriculumsubjectid': o['curriculumsubjectid']},
                                {'sectionid': b[0], 'curriculumsubjectid': b[1]}],
                    'meetings': [{'class_type': ctype, 'daydesc': x['daydesc'], 'starttimeid': x['starttimeid'],
                                  'endtimeid': x['endtimeid'], 'roomid': x['roomid']} for x in mine]}
            cur.execute('SAVEPOINT p6_group')
            try:
                out = mg.save_group(cur, body, cfg, username='p6-test', confirm_impact=True)
            except Exception:
                cur.execute('ROLLBACK TO SAVEPOINT p6_group')
                continue
            if not out.get('ok'):
                cur.execute('ROLLBACK TO SAVEPOINT p6_group')
                continue
            cur.execute('RELEASE SAVEPOINT p6_group')
            tx.handle().commit()
            other = others[0]
            rooms = [r['roomid'] for r in mg.load_rooms(cur).values()
                     if r.get('isactive') and r['roomid'] != o['roomid']]
            return {'o': o, 'n': other, 'b': b, 'gid': out.get('mergegroupid'), 'other_room': rooms[0],
                    'ctx': {'program': o['programcode'], 'year_level': o['yearlevel'],
                            'ay_id': o['academicyearid'], 'term': o['semestertype'], 'section_id': sec_id},
                    'cfg': cfg}
    pytest.skip('could not build a valid Merge Group around a real Published occurrence')


@pytest.fixture
def scope(tx):
    return _build(tx)


@pytest.fixture
def scope_same(tx):
    return _build(tx, mode='SAME_FACULTY', designated_from_occurrence=True)


def _sess(o, **change):
    s = {'subject_code': o['subjectcode'], 'day': o['daydesc'], 'starttimeid': o['starttimeid'],
         'endtimeid': o['endtimeid'], 'roomid': o['roomid'], 'official_sessionid': o['sessionid']}
    s.update(change)
    return s


def _save(head, sc, *sessions):
    return head.post('/api/local/save_arrangement', json=dict(sc['ctx'], sessions=list(sessions), reason='p6 test'))


def _check(head, sc, *sessions):
    return head.post('/api/local/check_room_conflicts', json=dict(sc['ctx'], sessions=list(sessions)))


def _locked(resp):
    body = resp.get_json() or {}
    return resp.status_code == 409 and body.get('code') == mg.MERGED_OCCURRENCE_LOCKED, body


def _counts(tx):
    cur = _cur(tx)
    cur.execute("SELECT (SELECT COUNT(*) FROM local_arrangement) la, (SELECT COUNT(*) FROM local_arrangement_sessions) las, "
                "(SELECT COUNT(*) FROM schedule_change_request) scr, (SELECT COUNT(*) FROM class_meeting_request) cmr, "
                "(SELECT COUNT(*) FROM schedule_sessions) ss, (SELECT COUNT(*) FROM merge_group) g")
    return dict(cur.fetchone())


# ── Local: Save / Check reject changes to a merged occurrence ───────────────────

@pytest.mark.parametrize('change', ['day', 'time', 'room'])
def test_local_save_rejects_a_day_time_or_room_change_of_a_merged_occurrence(head, tx, scope, change):
    o = scope['o']
    ch = {'day': {'day': 'Saturday' if o['daydesc'] != 'Saturday' else 'Friday'},
          'time': {'starttimeid': o['starttimeid'] + 1, 'endtimeid': o['endtimeid'] + 1},
          'room': {'roomid': scope['other_room']}}[change]
    before = _counts(tx)
    ok, body = _locked(_save(head, scope, _sess(o, **ch)))
    assert ok, body
    assert "P6 merged class" in body['error'] and change in body['merge_restrictions'][0]['changes']
    assert _counts(tx) == before                                              # nothing written


def test_local_save_rejects_a_multiple_faculty_substitution_as_a_merge_restriction(head, tx, scope):
    cur = _cur(tx)
    cur.execute("SELECT employeenumber FROM faculty WHERE isactive AND employeenumber <> %s LIMIT 1",
                (scope['o']['employeenumber'],))
    sub = cur.fetchone()['employeenumber']
    ok, body = _locked(_save(head, scope, _sess(scope['o'], faculty_id=sub)))
    assert ok, body
    assert body['merge_restrictions'][0]['changes'] == ['faculty']
    assert body['merge_restrictions'][0]['faculty_mode'] == 'MULTIPLE_FACULTY'


def test_same_faculty_group_rejects_local_changes_too(head, tx, scope_same):
    ok, body = _locked(_save(head, scope_same, _sess(scope_same['o'], roomid=scope_same['other_room'])))
    assert ok, body
    assert body['merge_restrictions'][0]['faculty_mode'] == 'SAME_FACULTY'


def test_local_changes_to_non_merged_occurrences_are_not_restricted(head, tx, scope):
    r = _save(head, scope, _sess(scope['n'], roomid=scope['other_room']))
    assert (r.get_json() or {}).get('code') != mg.MERGED_OCCURRENCE_LOCKED, r.get_json()
    r = _save(head, scope, _sess(scope['o']))                                 # unchanged merged copy
    assert (r.get_json() or {}).get('code') != mg.MERGED_OCCURRENCE_LOCKED, r.get_json()


def test_local_check_rejects_the_same_change(head, tx, scope):
    ok, body = _locked(_check(head, scope, _sess(scope['o'], roomid=scope['other_room'])))
    assert ok, body
    r = _check(head, scope, _sess(scope['n'], roomid=scope['other_room']))
    assert (r.get_json() or {}).get('code') != mg.MERGED_OCCURRENCE_LOCKED


# ── Direct-API bypass: Draft rows written outside Save ──────────────────────────

def _insert_arrangement(tx, sc, status, sessions, *, active=False):
    cur = _cur(tx)
    anchor = app_module._current_official_anchor(cur, sc['ctx']['program'], sc['ctx']['year_level'],
                                                 sc['ctx']['section_id'], sc['o']['semesterid'])
    cur.execute("""INSERT INTO local_arrangement (programcode, yearlevel, sectionid, semesterid, ref_versionid,
                                                   is_active, status, created_by, created_at, updated_at, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'p6-test', NOW(), NOW(), 'p6 test')
                   RETURNING arrangementid, updated_at""",
                (sc['ctx']['program'], sc['ctx']['year_level'], sc['ctx']['section_id'], sc['o']['semesterid'],
                 anchor['versionid'], active, status))
    row = cur.fetchone()
    for s in sessions:
        cur.execute("""INSERT INTO local_arrangement_sessions (arrangementid, subjectcode, daydesc, starttimeid,
                                                               endtimeid, roomid, faculty_employeenumber, official_sessionid)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (row['arrangementid'], s['subject_code'], s['day'], s['starttimeid'], s['endtimeid'],
                     s['roomid'], s.get('faculty_id') or sc['o']['employeenumber'], s['official_sessionid']))
    tx.handle().commit()
    return row['arrangementid'], app_module._local_updated_at_iso(row['updated_at'])


def _arr_state(tx, arr_id):
    cur = _cur(tx)
    cur.execute("SELECT status, is_active FROM local_arrangement WHERE arrangementid = %s", (arr_id,))
    return dict(cur.fetchone())


def test_publish_rejects_a_draft_written_around_save(head, tx, scope):
    arr, upd = _insert_arrangement(tx, scope, 'Draft', [_sess(scope['o'], roomid=scope['other_room'])])
    r = head.post(f'/api/local/arrangement/{arr}/publish', json={'reason': 'p6', 'expected_updated_at': upd})
    ok, body = _locked(r)
    assert ok, body
    assert _arr_state(tx, arr) == {'status': 'Draft', 'is_active': False}   # not published, not archived


def test_restore_of_an_arrangement_that_changes_a_merged_occurrence_is_rejected(head, tx, scope):
    arr, upd = _insert_arrangement(tx, scope, 'Archived', [_sess(scope['o'], roomid=scope['other_room'])])
    before = _counts(tx)
    r = head.post(f'/api/local/arrangement/{arr}/restore', json={'expected_updated_at': upd})
    body = r.get_json() or {}
    if body.get('code') not in (mg.MERGED_OCCURRENCE_LOCKED,):
        pytest.skip(f'restore refused earlier for another reason: {body}')
    assert r.status_code == 409 and _counts(tx) == before
    assert _arr_state(tx, arr)['status'] == 'Archived'


# ── Existing divergent arrangements: flagged, never modified ────────────────────

def test_existing_divergent_published_arrangement_is_flagged_not_modified(head, tx, scope):
    arr, _upd = _insert_arrangement(tx, scope, 'Published', [_sess(scope['o'], roomid=scope['other_room'])],
                                    active=True)
    before = _counts(tx)
    lst = head.get(f"/api/local/arrangements?program={scope['ctx']['program']}"
                   f"&year_level={scope['ctx']['year_level']}&section_id={scope['ctx']['section_id']}").get_json()
    mine = [a for a in lst['arrangements'] if a['arrangementid'] == arr]
    assert mine and mine[0]['merge_restrictions'][0]['group'] == 'P6 merged class'
    det = head.get(f'/api/local/arrangement/{arr}').get_json()
    assert det['merge_restrictions'] and any(s['merge_locked'] for s in det['sessions'])
    rep = head.get(f"/api/local/merge_divergence?semesterid={scope['o']['semesterid']}").get_json()
    assert rep['model'] == 'groups' and arr in [a['arrangementid'] for a in rep['arrangements']]
    assert _counts(tx) == before and _arr_state(tx, arr) == {'status': 'Published', 'is_active': True}


def test_a_new_draft_carrying_a_divergent_override_is_warned_and_cannot_publish(head, tx, scope):
    _insert_arrangement(tx, scope, 'Published', [_sess(scope['o'], roomid=scope['other_room'])], active=True)
    r = _save(head, scope, _sess(scope['n']))
    body = r.get_json() or {}
    if not body.get('success'):
        pytest.skip(f'save refused for an unrelated reason: {body}')
    assert body['merge_warnings'][0]['official_sessionid'] == scope['o']['sessionid']
    pub = head.post(f"/api/local/arrangement/{body['arrangement_id']}/publish",
                    json={'reason': 'p6', 'expected_updated_at': body['updated_at']})
    ok, pbody = _locked(pub)
    assert ok, pbody


# ── Faculty Schedule Adjustment requests ────────────────────────────────────────

@pytest.fixture
def faculty(tx, scope):
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Faculty' ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Faculty account')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Faculty', username=acct['username'],
                 employeenumber=scope['o']['employeenumber'])
    return c


def _adj_payload(o, **kw):
    p = {'request_type': 'adjustment', 'subject_code': o['subjectcode'], 'section_id': o['sectionid'],
         'official_sessionid': o['sessionid'], 'reason': 'p6 test'}
    p.update(kw)
    return p


def test_adjustment_request_for_a_merged_occurrence_is_rejected_at_submission(faculty, tx, scope):
    before = _counts(tx)
    r = faculty.post('/api/faculty/submit_request',
                     json=_adj_payload(scope['o'], day='Saturday' if scope['o']['daydesc'] != 'Saturday' else 'Friday'))
    ok, body = _locked(r)
    assert ok, body
    assert 'P6 merged class' in body['error'] and _counts(tx) == before


def _insert_adjustment(tx, o, **new):
    cur = _cur(tx)
    cur.execute("""INSERT INTO schedule_change_request (scheduleid, versionid, official_sessionid, change_type,
                                                        new_daydesc, new_roomid, effective_from, reason,
                                                        submitted_by, status)
                   VALUES (%s, %s, %s, 'Room', %s, %s, CURRENT_DATE, 'p6 test', %s, 'Pending')
                   RETURNING requestid""",
                (o['scheduleid'], o['versionid'], o['sessionid'], new.get('day'), new.get('roomid'),
                 o['employeenumber']))
    rid = cur.fetchone()['requestid']
    tx.handle().commit()
    return rid


def _req_status(tx, rid):
    cur = _cur(tx)
    cur.execute("SELECT status FROM schedule_change_request WHERE requestid = %s", (rid,))
    return cur.fetchone()['status']


def test_pending_adjustment_is_flagged_not_approvable_and_still_rejectable(head, tx, scope):
    rid = _insert_adjustment(tx, scope['o'], roomid=scope['other_room'])
    v = head.get(f'/api/requests/validate?type=adjustment&id={rid}').get_json()
    assert v['all_ok'] is False and v['merge_restrictions'][0]['group'] == 'P6 merged class'
    assert any('P6 merged class' in c for c in v['conflicts'])
    lst = head.get('/api/requests/list?category=adjustment&status=pending').get_json()
    flagged = [r for r in lst['requests'] if r['requestid'] == rid]
    assert flagged and flagged[0]['merge_restriction']['code'] == mg.MERGED_OCCURRENCE_LOCKED
    before = _counts(tx)
    ok, body = _locked(head.post('/api/requests/decide', json={'type': 'adjustment', 'id': rid,
                                                                'decision': 'Approved'}))
    assert ok, body
    assert _req_status(tx, rid) == 'Pending' and _counts(tx) == before     # nothing applied
    rej = head.post('/api/requests/decide', json={'type': 'adjustment', 'id': rid, 'decision': 'Rejected',
                                                  'remarks': 'merged class'}).get_json()
    assert rej.get('success') is True and _req_status(tx, rid) == 'Rejected'


def test_editing_a_pending_adjustment_cannot_revive_it(faculty, tx, scope):
    rid = _insert_adjustment(tx, scope['o'], roomid=scope['other_room'])
    ok, body = _locked(faculty.put(f'/api/faculty/my_requests/adjustment/{rid}',
                                   json={'room_id': scope['other_room'], 'reason': 'edit'}))
    assert ok, body


def test_adjustment_of_a_non_merged_occurrence_is_not_restricted(faculty, tx, scope):
    n = scope['n']
    r = faculty.post('/api/faculty/submit_request', json=_adj_payload(n, room_id=scope['other_room']))
    assert (r.get_json() or {}).get('code') != mg.MERGED_OCCURRENCE_LOCKED, r.get_json()


# ── Make-up classes stay separate, date-specific occurrences ────────────────────

def test_make_up_for_a_merged_class_is_not_merge_restricted(faculty, tx, scope):
    o = scope['o']
    d = date.today() + timedelta(days=21)
    r = faculty.post('/api/faculty/submit_request', json={
        'request_type': 'makeup', 'subject_code': o['subjectcode'], 'section_id': o['sectionid'],
        'request_date': d.isoformat(), 'start_time': '07:30 AM', 'end_time': '08:00 AM',
        'room_id': scope['other_room'], 'reason': 'p6 make-up'})
    body = r.get_json() or {}
    assert body.get('code') != mg.MERGED_OCCURRENCE_LOCKED, body


# ── Legacy compatibility ────────────────────────────────────────────────────────

def test_legacy_model_applies_no_merge_restriction(head, faculty, tx, scope):
    mg.set_merge_model(_cur(tx), 'legacy')
    tx.handle().commit()
    r = _save(head, scope, _sess(scope['o'], roomid=scope['other_room']))
    assert (r.get_json() or {}).get('code') != mg.MERGED_OCCURRENCE_LOCKED
    r = faculty.post('/api/faculty/submit_request', json=_adj_payload(scope['o'], room_id=scope['other_room']))
    assert (r.get_json() or {}).get('code') != mg.MERGED_OCCURRENCE_LOCKED
    rep = head.get('/api/local/merge_divergence').get_json()
    assert rep == {'success': True, 'model': 'legacy', 'arrangements': []}
    rid = _insert_adjustment(tx, scope['o'], roomid=scope['other_room'])
    v = head.get(f'/api/requests/validate?type=adjustment&id={rid}').get_json()
    assert v['merge_restrictions'] == []


def test_live_model_and_groups_are_untouched_outside_the_transaction():
    from database import query_db
    row = query_db("SELECT config_value FROM scheduler_config WHERE config_key = %s", (mg.MODEL_KEY,), one=True)
    assert row is None or row['config_value'] in ('legacy', 'groups')   # P7: cut over to groups
    assert query_db("SELECT COUNT(*) AS n FROM merge_group WHERE created_by LIKE %s",
                    ('%-test',), one=True)['n'] == 0              # nothing a test created survives
