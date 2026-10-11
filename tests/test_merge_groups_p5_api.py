"""HC16 group model — P5 Manual Editor endpoints against the real database.

Every test runs inside ONE always-rolled-back transaction (tests/_shared_tx.py); the
switch to hc_merge_model='groups', the Merge Group and any schedule rows exist only
inside it.
"""
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import merge_groups as mg
from conftest import requires_db
from test_merge_groups_p2_api import _context, _make_group, _other_room, _row, head, tx, world  # noqa: F401

pytestmark = requires_db


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


def _counts(tx):
    cur = _cur(tx)
    cur.execute("SELECT (SELECT COUNT(*) FROM schedule) s, (SELECT COUNT(*) FROM schedule_version) v, "
                "(SELECT COUNT(*) FROM schedule_sessions) ss, (SELECT COUNT(*) FROM merge_group) g, "
                "(SELECT COUNT(*) FROM merge_group_member) gm, (SELECT COUNT(*) FROM merge_group_meeting) m, "
                "(SELECT COUNT(*) FROM scheduler_config) c")
    return dict(cur.fetchone())


def _editor_row(w, key='1', *, room=None, fac='', day=None):
    """A slice exactly as the editor posts it (12-hour labels, string room id)."""
    return {'key': key, 'subject_code': w['subject']['subjectcode'], 'day': day or w['day'],
            'start_time': mg.label12(w['start']), 'end_time': mg.label12(w['end']),
            'room_id': str(room if room is not None else w['room']['roomid']), 'faculty_id': fac}


def _ctx(head, w, rows, which='a', code=None):
    r = head.post('/api/manual/merge_context', json={
        'ay_id': w['sem']['academicyearid'], 'semester': w['sem']['semestertype'],
        'section_id': w[which][0], 'subject_code': code or w['subject']['subjectcode'], 'rows': rows})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _publish(tx, w, which, fac, *, room=None, status='Published', day=None):
    cur = _cur(tx)
    sid = app_module._find_or_create_schedule(cur, w[which][1], w[which][0], w['sem']['semesterid'], fac, 'Published')
    cur.execute("INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status, "
                "employeenumber) VALUES (%s, 9001, %s, 'manual_editor', %s, %s) RETURNING versionid",
                (sid, status, status, fac))
    vid = cur.fetchone()['versionid']
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING sessionid",
                (vid, day or w['day'], w['stid'], w['etid'], room if room is not None else w['room']['roomid']))
    ssid = cur.fetchone()['sessionid']
    tx.handle().commit()
    return vid, ssid


# ── Legacy vs group model ───────────────────────────────────────────────────────

def test_merge_context_is_legacy_under_the_legacy_model(head, tx, world):
    _make_group(tx, world)
    mg.set_merge_model(_cur(tx), 'legacy')
    tx.handle().commit()
    body = _ctx(head, world, [_editor_row(world)])
    assert body == {'success': True, 'model': 'legacy'}


def test_editor_page_carries_the_internal_model_and_loads_the_group_module(head, tx, world):
    html = head.get('/schedule/manual-editor').get_data(as_text=True)
    if 'app-init-data' not in html:
        pytest.skip('editor route not reachable for this account')
    assert 'data-merge-model="groups"' in html and 'manualEditor.mergeGroups.js' in html
    mg.set_merge_model(_cur(tx), 'legacy')
    tx.handle().commit()
    assert 'data-merge-model="legacy"' in head.get('/schedule/manual-editor').get_data(as_text=True)


def test_merge_context_requires_a_login(tx, world):
    c = app_module.app.test_client()
    assert c.post('/api/manual/merge_context', json={}).status_code == 401


# ── Metadata, read-only ─────────────────────────────────────────────────────────

def test_merge_context_gives_badge_details_and_in_sync_slice_without_writing(head, tx, world):
    _make_group(tx, world)
    before = _counts(tx)
    body = _ctx(head, world, [_editor_row(world)])
    s = body['current']
    assert body['model'] == 'groups' and s['group_name'] == 'P2 merged class' and s['section_count'] == 2
    assert len(s['sections']) == 2 and s['status'] == 'merged' and s['faculty_mode'] == 'MULTIPLE_FACULTY'
    (m,) = s['meetings']
    assert (m['day'], m['start_label'], m['roomid']) == (world['day'], mg.label12(world['start']),
                                                         world['room']['roomid'])
    assert body['rows']['1']['sync'] == 'in_sync' and body['rows']['1']['event_key'] == m['event_key']
    assert _counts(tx) == before


def test_non_member_subject_has_no_current_entry(head, tx, world):
    _make_group(tx, world)
    body = _ctx(head, world, [], code='NOT A MEMBER 999')
    assert body['model'] == 'groups' and body['current'] is None


def test_editor_slice_off_the_merged_slot_is_a_separate_class(head, tx, world):
    _make_group(tx, world)
    other = _other_room(tx, world)['roomid']
    body = _ctx(head, world, [_editor_row(world, room=other)])
    s, r = body['current'], body['rows']['1']
    assert r['sync'] == 'separate' and r['event_key'] is None and r['actual']
    assert s['status'] == 'not_merged' and 'reset_plan' not in s and 'can_reset' not in s


def test_allowed_but_unmerged_group_is_not_merged(head, tx, world):
    _make_group(tx, world, meetings=False)
    body = _ctx(head, world, [_editor_row(world)])
    s = body['current']
    assert s['status'] == 'not_merged' and s['meetings'] == [] and len(s['sections']) == 2
    assert body['rows']['1']['sync'] == 'separate'


def test_merged_with_names_the_other_section_at_the_slot(head, tx, world):
    _make_group(tx, world)
    _publish(tx, world, 'b', world['facs'][0])
    body = _ctx(head, world, [_editor_row(world)])
    b_label = world['sec'][world['b'][0]]['label']
    assert body['rows']['1']['merged_with'] == [b_label]
    assert body['current']['meetings'][0]['sections'] == [b_label]


def test_same_faculty_designee_is_server_provided(head, tx, world):
    fac = world['facs'][0]
    _make_group(tx, world, mode='SAME_FACULTY', designated=fac)
    body = _ctx(head, world, [_editor_row(world, fac=world['facs'][1])])
    s = body['current']
    assert 'faculty_controlled' not in s and s['designated_faculty']['id'] == fac   # P7: never locked
    assert s['designated_faculty']['name'] and body['rows']['1']['faculty_issue']


def test_same_faculty_conflict_with_the_other_sections_published_faculty(head, tx, world):
    _make_group(tx, world, mode='SAME_FACULTY')
    _publish(tx, world, 'b', world['facs'][0])
    body = _ctx(head, world, [_editor_row(world, fac=world['facs'][1])])
    assert body['rows']['1']['faculty_issue']
    ok = _ctx(head, world, [_editor_row(world, fac=world['facs'][0])])
    assert ok['rows']['1']['faculty_issue'] is None


def test_client_class_type_never_changes_the_classification(head, tx, world):
    _make_group(tx, world)
    plain = _ctx(head, world, [_editor_row(world)])['rows']['1']
    typed = _ctx(head, world, [dict(_editor_row(world), class_type='Lab')])['rows']['1']
    assert plain['sync'] == typed['sync'] == 'in_sync' and plain['event_key'] == typed['event_key']


def test_merge_context_failure_is_reported_and_recovers(head, tx, world, monkeypatch):
    _make_group(tx, world)

    def boom(*a, **k):
        raise RuntimeError('database unavailable')
    real = mg.load_editor_context
    monkeypatch.setattr(mg, 'load_editor_context', boom)
    r = head.post('/api/manual/merge_context', json={
        'ay_id': world['sem']['academicyearid'], 'semester': world['sem']['semestertype'],
        'section_id': world['a'][0], 'subject_code': world['subject']['subjectcode'], 'rows': []})
    assert r.status_code == 500 and r.get_json()['success'] is False and 'unavailable' in r.get_json()['error']
    monkeypatch.setattr(mg, 'load_editor_context', real)
    assert _ctx(head, world, [_editor_row(world)])['current']['status'] == 'merged'


# ── Occupancy: same event exempt, outsiders block ───────────────────────────────

def _q(w):
    return f"ay_id={w['sem']['academicyearid']}&semester={w['sem']['semestertype']}"


def test_room_schedule_rows_carry_the_server_event_key(head, tx, world):
    _make_group(tx, world)
    _publish(tx, world, 'b', world['facs'][0])
    key = _ctx(head, world, [_editor_row(world)])['rows']['1']['event_key']
    rows = head.get(f"/api/get_room_schedule/{world['room']['roomid']}?{_q(world)}").get_json()
    mine = [r for r in rows if r.get('section_id') == world['b'][0] and r['daydesc'] == world['day']
            and r['starttimeid'] == world['stid']]
    assert mine and all(r['merge_event'] == key for r in mine)
    assert all(r['merge_event'] is None for r in rows if r not in mine
               and mg.norm_code(r.get('subjectcode')) != mg.norm_code(world['subject']['subjectcode']))


def test_room_occupancy_by_day_and_faculty_schedule_carry_keys(head, tx, world):
    fac = world['facs'][0]
    _make_group(tx, world)
    _publish(tx, world, 'b', fac)
    key = _ctx(head, world, [_editor_row(world)])['rows']['1']['event_key']
    occ = head.get(f"/api/rooms_occupancy_by_day?day={world['day']}&{_q(world)}").get_json()
    ranges = occ[str(world['room']['roomid'])]
    assert any(r.get('merge_event') == key and r.get('section_id') == world['b'][0] for r in ranges)
    fs = head.get(f"/api/manual/faculty_schedule?emp_num={fac}&{_q(world)}").get_json()
    hit = [r for r in fs if r.get('section_id') == world['b'][0] and r['daydesc'] == world['day']]
    assert hit and hit[0]['merge_event'] == key and hit[0]['roomid'] == world['room']['roomid']


def test_outsider_in_the_group_room_has_no_key(head, tx, world):
    _make_group(tx, world)
    cur = _cur(tx)
    cur.execute("SELECT sv.versionid FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid "
                "WHERE sv.status = 'Published' AND s.semesterid = %s AND s.sectionid <> ALL(%s) "
                "ORDER BY sv.versionid LIMIT 1",
                (world['sem']['semesterid'], [world['a'][0], world['b'][0]]))
    other = cur.fetchone()
    if not other:
        pytest.skip('no outsider Published version')
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, %s)",
                (other['versionid'], world['day'], world['stid'], world['etid'], world['room']['roomid']))
    tx.handle().commit()
    rows = head.get(f"/api/get_room_schedule/{world['room']['roomid']}?{_q(world)}").get_json()
    outs = [r for r in rows if r['daydesc'] == world['day'] and r['starttimeid'] == world['stid']
            and r.get('section_id') not in (world['a'][0], world['b'][0])]
    assert outs and all(r['merge_event'] is None for r in outs)


def test_legacy_occupancy_rows_have_no_merge_keys(head, tx, world):
    _make_group(tx, world)
    _publish(tx, world, 'b', world['facs'][0])
    mg.set_merge_model(_cur(tx), 'legacy')
    tx.handle().commit()
    rows = head.get(f"/api/get_room_schedule/{world['room']['roomid']}?{_q(world)}").get_json()
    assert rows and not any('merge_event' in r for r in rows)
    occ = head.get(f"/api/rooms_occupancy_by_day?day={world['day']}&{_q(world)}").get_json()
    assert not any('merge_event' in r for rs in occ.values() for r in rs)


# ── Save Draft / Publish with editor-shaped rows ────────────────────────────────

def _editor_save_row(w, *, room=None, fac='TBA'):
    r = _row(w, room=room, fac=fac)
    r.update(start_time=mg.label12(w['start']), end_time=mg.label12(w['end']))
    return r


def test_editor_rows_save_on_and_off_the_merged_slot(head, tx, world):
    _make_group(tx, world)
    ok = head.post('/api/schedule/save-draft', json={'context': _context(world),
                                                      'schedule_data': [_editor_save_row(world)]}).get_json()
    assert ok.get('success') is True, ok
    off = head.post('/api/schedule/save-draft', json={
        'context': _context(world),
        'schedule_data': [_editor_save_row(world, room=_other_room(tx, world)['roomid'])]}).get_json()
    assert off.get('success') is True, off                       # P7: an ordinary class, never blocked
    assert not any(w.get('rule') == 'HC16' for w in off.get('draft_warnings') or [])


def test_save_draft_writes_only_the_current_section(head, tx, world):
    _make_group(tx, world)
    cur = _cur(tx)

    def n(which):
        cur.execute("SELECT COUNT(*) AS n FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid "
                    "WHERE s.sectionid = %s AND s.semesterid = %s", (world[which][0], world['sem']['semesterid']))
        return cur.fetchone()['n']
    b_before = n('b')
    head.post('/api/schedule/save-draft', json={'context': _context(world),
                                                 'schedule_data': [_editor_save_row(world)]})
    assert n('b') == b_before


def test_save_draft_of_an_allowed_but_unmerged_member_has_no_hc16_warning(head, tx, world):
    _make_group(tx, world, meetings=False)
    body = head.post('/api/schedule/save-draft', json={'context': _context(world),
                                                        'schedule_data': [_editor_save_row(world)]}).get_json()
    assert body.get('success') is True, body
    assert not any(w.get('rule') == 'HC16' for w in body.get('draft_warnings') or [])


def test_publish_of_the_group_slot_raises_no_hc16_finding(head, tx, world):
    _make_group(tx, world)
    r = head.post('/api/schedule/approve', json={'context': dict(_context(world), sectionId=world['a'][0]),
                                                  'schedule_data': [_editor_save_row(world)]})
    assert not any(v.get('rule') == 'HC16' for v in r.get_json().get('violations') or []), r.get_json()


def _sessions_of(tx, w, which):
    cur = _cur(tx)
    cur.execute("SELECT sv.versionid, sv.status, ss.sessionid, ss.daydesc, ss.starttimeid, ss.endtimeid, ss.roomid "
                "FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid "
                "LEFT JOIN schedule_sessions ss ON ss.versionid = sv.versionid "
                "WHERE s.sectionid = %s AND s.semesterid = %s ORDER BY sv.versionid, ss.sessionid",
                (w[which][0], w['sem']['semesterid']))
    return [dict(r) for r in cur.fetchall()]


def test_removing_this_members_class_leaves_the_other_member_unchanged(head, tx, world):
    _make_group(tx, world)
    _publish(tx, world, 'a', world['facs'][0])
    _publish(tx, world, 'b', world['facs'][0])
    b_before = _sessions_of(tx, world, 'b')
    body = head.post('/api/schedule/save-draft', json={
        'context': _context(world), 'schedule_data': [],
        'removed_subjects': [world['subject']['subjectcode']]}).get_json()
    assert body.get('success') is True, body
    assert _sessions_of(tx, world, 'b') == b_before
    statuses = {r['status'] for which in ('a', 'b') for r in _sessions_of(tx, world, which)}
    assert statuses <= {'Draft', 'Published', 'Archive'}                 # no "Approved"
    key = _ctx(head, world, [_editor_row(world)], which='b')['rows']['1']
    assert key['sync'] == 'in_sync'


@pytest.mark.parametrize('n', [3, 5])
def test_three_and_more_section_groups_report_every_section(head, tx, world, n):
    cur = _cur(tx)
    code = mg.norm_code(world['subject']['subjectcode'])
    sections, subjects = mg.load_offered(cur, world['sem']['semesterid'])
    occ = mg.fetch_occurrences(cur, [world['sem']['semesterid']])
    busy = {(o['sectionid'], mg.norm_code(o['subjectcode'])) for o in occ}
    extra = [(sid, cs) for sid, sec in sections.items() if sec['isactive'] and sid not in (world['a'][0], world['b'][0])
             for cs in sec['offered'] if mg.norm_code(subjects[cs]['subjectcode']) == code and (sid, code) not in busy]
    if len(extra) < n - 2:
        pytest.skip(f'needs {n} sections offering {code}')
    body = {'groupname': 'P5 big group', 'semesterid': world['sem']['semesterid'],
            'ref_curriculumsubjectid': world['a'][1], 'faculty_mode': 'MULTIPLE_FACULTY', 'employeenumber': None,
            'members': [{'sectionid': s, 'curriculumsubjectid': c}
                        for s, c in [world['a'], world['b']] + extra[:n - 2]],
            'meetings': [{'class_type': 'Lecture', 'daydesc': world['day'], 'starttimeid': world['stid'],
                          'endtimeid': world['etid'], 'roomid': world['room']['roomid']}]}
    out = mg.save_group(cur, body, world['cfg'], username='p5-test', confirm_impact=True)
    if not out['ok']:
        pytest.skip(f'group not valid with these sections: {out}')
    tx.handle().commit()
    s = _ctx(head, world, [_editor_row(world)])['current']
    assert s['section_count'] == n and len(s['sections']) == n and s['status'] == 'merged'


# ── Version restore never rewrites history ──────────────────────────────────────

def test_restored_draft_off_the_slot_is_a_separate_class_and_publishes(head, tx, world):
    _make_group(tx, world)
    other = _other_room(tx, world)['roomid']
    vid, ssid = _publish(tx, world, 'a', world['facs'][0], room=other, status='Archive')
    r = head.post(f'/api/schedule/versions/{vid}/restore', json={'restore_mode': 'draft'}).get_json()
    assert r.get('success') is True, r
    cur = _cur(tx)
    cur.execute("SELECT roomid FROM schedule_sessions WHERE sessionid = %s", (ssid,))
    assert cur.fetchone()['roomid'] == other                                # history untouched
    cur.execute("SELECT ss.roomid FROM schedule_sessions ss JOIN schedule_version sv ON sv.versionid = ss.versionid "
                "JOIN schedule s ON s.scheduleid = sv.scheduleid WHERE sv.status = 'Draft' AND s.sectionid = %s "
                "AND s.semesterid = %s", (world['a'][0], world['sem']['semesterid']))
    assert [x['roomid'] for x in cur.fetchall()] == [other]                 # restored as is
    body = _ctx(head, world, [_editor_row(world, room=other)])
    assert body['rows']['1']['sync'] == 'separate'
    pub = head.post('/api/schedule/approve', json={'context': dict(_context(world), sectionId=world['a'][0]),
                                                    'schedule_data': [_row(world, room=other)]})
    assert not any(v.get('rule') == 'HC16' for v in pub.get_json().get('violations') or []), pub.get_json()


def test_live_model_and_groups_are_untouched_outside_the_transaction():
    from database import query_db
    row = query_db("SELECT config_value FROM scheduler_config WHERE config_key = %s", (mg.MODEL_KEY,), one=True)
    assert row is None or row['config_value'] in ('legacy', 'groups')   # P7: cut over to groups
    assert query_db("SELECT COUNT(*) AS n FROM merge_group WHERE created_by LIKE %s",
                    ('%-test',), one=True)['n'] == 0              # nothing a test created survives
