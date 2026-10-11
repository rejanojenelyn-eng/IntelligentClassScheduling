"""HC16 Merge Groups — P1 endpoint + schema tests against the real database.

Every test runs inside ONE transaction that is always rolled back
(tests/_shared_tx.py), so nothing is persisted. Covers CRUD, the impact
confirmation, deactivate/activate/delete, read-only ended semesters, the
database-level single-active-membership and override-audit guarantees, the
discovery/dry-run previews, and protection of the internal hc_merge_model switch
and the frozen legacy keys on the Settings hard-constraint endpoint.
"""
from datetime import date

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import merge_groups as mg
from conftest import requires_db

pytestmark = requires_db


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


def _client_for(tx, role):
    """A logged-in test client for an existing active account of `role` (the
    account-status before_request hook re-checks the account on every request)."""
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = %s ORDER BY userid LIMIT 1", (role,))
    acct = cur.fetchone()
    if not acct:
        pytest.skip(f'no active {role} account')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['loggedin'] = True
        s['role'] = role
        s['username'] = acct['username']
    return c, acct['username']


@pytest.fixture
def admin(tx):
    c, username = _client_for(tx, 'Admin')
    c.username = username
    return c


@pytest.fixture
def data(tx):
    """An active semester and a lecture-only subject code offered (same code) to 3+
    sections, split into sections with and without existing sessions of it."""
    cur = _cur(tx)
    mg.ensure_schema(cur)
    cur.execute("SELECT semesterid FROM semester WHERE isactive ORDER BY semesterid LIMIT 1")
    row = cur.fetchone()
    if not row:
        pytest.skip('no active semester')
    sem = row['semesterid']
    sections, subjects = mg.load_offered(cur, sem)
    by_code = {}
    for sid, sec in sections.items():
        if not sec['isactive']:
            continue
        for csid in sec['offered']:
            s = subjects[csid]
            if list(mg.required_parts(s)) == ['Lecture']:
                by_code.setdefault(mg.norm_code(s['subjectcode']), []).append((sid, csid))
    occ = mg.fetch_occurrences(cur, [sem])
    busy = {(o['sectionid'], mg.norm_code(o['subjectcode'])) for o in occ}
    pick = None
    for code, pairs in sorted(by_code.items(), key=lambda kv: -len(kv[1])):
        free = [p for p in pairs if (p[0], code) not in busy]
        if len(free) >= 3:
            pick = (code, pairs, free, [p for p in pairs if (p[0], code) in busy])
            break
    if not pick:
        pytest.skip('no subject offered to 3+ sections without existing sessions')
    code, pairs, free, scheduled = pick
    cur.execute("UPDATE merge_group SET is_active = FALSE WHERE semesterid = %s", (sem,))
    cur.execute("UPDATE merge_group_member SET is_active = FALSE WHERE mergegroupid IN "
                "(SELECT mergegroupid FROM merge_group WHERE semesterid = %s)", (sem,))
    tx.handle().commit()
    return {'sem': sem, 'code': code, 'free': free, 'scheduled': scheduled,
            'hours': mg.required_parts(subjects[free[0][1]])['Lecture']}


def _payload(data, members, name='P1 Test Group', **over):
    body = {'groupname': name, 'semesterid': data['sem'], 'ref_curriculumsubjectid': members[0][1],
            'faculty_mode': 'MULTIPLE_FACULTY', 'is_active': True,
            'members': [{'sectionid': s, 'curriculumsubjectid': c} for s, c in members], 'meetings': []}
    body.update(over)
    return body


def _count_groups(tx, sem):
    cur = _cur(tx)
    cur.execute("SELECT COUNT(*) AS n FROM merge_group WHERE semesterid = %s", (sem,))
    return cur.fetchone()['n']


def _block(admin, data):
    opts = admin.get(f"/admin/settings/merge_groups/options?semesterid={data['sem']}").get_json()
    want = data['hours'] * 60
    for b in opts['blocks']:
        h1, m1 = map(int, b['start'].split(':'))
        h2, m2 = map(int, b['end'].split(':'))
        if (h2 * 60 + m2) - (h1 * 60 + m1) == want:
            room = next((r for r in opts['rooms'] if r['roomtype'] == 'Lecture'), None)
            return b, room
    pytest.skip(f"no HC6 block of {data['hours']}h")


# ── Access ──────────────────────────────────────────────────────────────────────

def test_endpoints_are_admin_only(tx):
    c, _ = _client_for(tx, 'Academic Head')
    assert c.get('/admin/settings/merge_groups').status_code == 403
    assert c.post('/admin/settings/merge_groups', json={}).status_code == 403
    assert c.get('/admin/settings/merge_groups/dry_run').status_code == 403


# ── CRUD ────────────────────────────────────────────────────────────────────────

def test_create_unscheduled_group_and_list_it(admin, tx, data):
    r = admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][:3]))
    body = r.get_json()
    assert r.status_code == 201, body
    assert body['state'] == 'UNSCHEDULED'
    listing = admin.get(f"/admin/settings/merge_groups?semesterid={data['sem']}").get_json()
    assert listing['model'] in ('legacy', 'groups')   # P7: live DB cut over to groups
    g = next(g for g in listing['groups'] if g['mergegroupid'] == body['mergegroupid'])
    assert g['state'] == 'UNSCHEDULED' and g['config_errors'] == [] and len(g['members']) == 3
    assert {m['equivalence_basis'] for m in g['members']} <= {'reference', 'same_code'}
    assert g['origin'] == 'admin' and g['created_by'] == admin.username


def test_validation_errors_are_returned_not_saved(admin, tx, data):
    before = _count_groups(tx, data['sem'])
    r = admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][:1]))
    assert r.status_code == 400 and any('at least 2' in e for e in r.get_json()['errors'])
    assert _count_groups(tx, data['sem']) == before


def test_a_section_subject_belongs_to_one_active_group_only(admin, tx, data):
    assert admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][:2])).status_code == 201
    r = admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][1:3], name='Second group'))
    assert r.status_code == 400
    assert any('only one active group' in e for e in r.get_json()['errors'])
    inactive = _payload(data, data['free'][1:3], name='Second group', is_active=False)
    assert admin.post('/admin/settings/merge_groups', json=inactive).status_code == 201


def test_database_enforces_single_active_membership_and_override_audit(admin, tx, data):
    gid = admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][:2])).get_json()['mergegroupid']
    other = admin.post('/admin/settings/merge_groups',
                       json=_payload(data, data['free'][1:3], name='Other', is_active=False)).get_json()['mergegroupid']
    cur = _cur(tx)
    sid, csid = data['free'][0]
    cur.execute('SAVEPOINT p1_uq')
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("""INSERT INTO merge_group_member (mergegroupid, sectionid, curriculumsubjectid,
                       equivalence_basis, is_active) VALUES (%s, %s, %s, 'same_code', TRUE)""", (other, sid, csid))
    cur.execute('ROLLBACK TO SAVEPOINT p1_uq')
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("""UPDATE merge_group_member SET equivalence_basis = 'admin_override'
                       WHERE mergegroupid = %s""", (gid,))
    cur.execute('ROLLBACK TO SAVEPOINT p1_uq')


def test_update_adds_meetings_and_keeps_meeting_identity(admin, tx, data):
    gid = admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][:3])).get_json()['mergegroupid']
    block, room = _block(admin, data)
    meeting = {'class_type': 'Lecture', 'daydesc': 'Sunday', 'starttimeid': block['starttimeid'],
               'endtimeid': block['endtimeid'], 'roomid': room['roomid'] if room else None}
    r = admin.put(f'/admin/settings/merge_groups/{gid}', json=_payload(data, data['free'][:3], meetings=[meeting]))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['state'] == 'SCHEDULED'
    cur = _cur(tx)
    cur.execute("SELECT mergegroupmeetingid FROM merge_group_meeting WHERE mergegroupid = %s", (gid,))
    first = cur.fetchone()['mergegroupmeetingid']
    renamed = _payload(data, data['free'][:3], name='Renamed group', meetings=[dict(meeting, roomid=None)])
    assert admin.put(f'/admin/settings/merge_groups/{gid}', json=renamed).status_code == 200
    cur.execute("SELECT mergegroupmeetingid, roomid FROM merge_group_meeting WHERE mergegroupid = %s", (gid,))
    row = cur.fetchone()
    assert row['mergegroupmeetingid'] == first and row['roomid'] is None   # same slot, same event identity


def test_group_cannot_move_semester_and_unknown_group_is_404(admin, tx, data):
    gid = admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][:2])).get_json()['mergegroupid']
    moved = _payload(data, data['free'][:2], semesterid=data['sem'] + 1000)
    r = admin.put(f'/admin/settings/merge_groups/{gid}', json=moved)
    assert r.status_code == 400 and any('different semester' in e for e in r.get_json()['errors'])
    assert admin.put('/admin/settings/merge_groups/99999999', json=_payload(data, data['free'][:2])).status_code == 404


def test_preview_validates_without_writing(admin, tx, data):
    before = _count_groups(tx, data['sem'])
    r = admin.post('/admin/settings/merge_groups/preview', json=_payload(data, data['free'][:2]))
    body = r.get_json()
    assert r.status_code == 200 and body['valid'] is True and body['state'] == 'UNSCHEDULED'
    assert _count_groups(tx, data['sem']) == before


def test_member_candidates_come_with_server_side_equivalence(admin, tx, data):
    sid, csid = data['free'][0]
    body = admin.get(f"/admin/settings/merge_groups/member_candidates?semesterid={data['sem']}"
                     f"&ref_curriculumsubjectid={csid}").get_json()
    assert body['success']
    mine = next(s for s in body['sections'] if s['sectionid'] == sid)
    assert any(o['basis'] == 'reference' for o in mine['options'])


def test_new_group_without_meetings_is_an_allowance_and_needs_no_confirmation(admin, tx, data):
    # P7: allowing scheduled sections to merge changes nothing about their classes.
    if len(data['scheduled']) < 2:
        pytest.skip('needs 2 sections that already have sessions of the subject')
    body = _payload(data, data['scheduled'][:2], name='Impact group')
    before = _count_groups(tx, data['sem'])
    r = admin.post('/admin/settings/merge_groups', json=body)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['impact']['newly_diverging'] == []
    assert _count_groups(tx, data['sem']) == before + 1


def test_deactivate_activate_and_delete(admin, tx, data):
    gid = admin.post('/admin/settings/merge_groups', json=_payload(data, data['free'][:2])).get_json()['mergegroupid']
    assert admin.post(f'/admin/settings/merge_groups/{gid}/active', json={'is_active': False}).status_code == 200
    cur = _cur(tx)
    cur.execute("SELECT bool_or(is_active) AS any_active FROM merge_group_member WHERE mergegroupid = %s", (gid,))
    assert cur.fetchone()['any_active'] is False          # members mirror the group's state
    assert admin.post(f'/admin/settings/merge_groups/{gid}/active', json={'is_active': True}).status_code == 200
    assert admin.delete(f'/admin/settings/merge_groups/{gid}').status_code == 200
    cur.execute("SELECT COUNT(*) AS n FROM merge_group_member WHERE mergegroupid = %s", (gid,))
    assert cur.fetchone()['n'] == 0


def test_ended_semester_groups_are_read_only(admin, tx, data):
    cur = _cur(tx)
    cur.execute("SELECT semesterid FROM semester WHERE semenddate < %s ORDER BY semesterid LIMIT 1", (date.today(),))
    row = cur.fetchone()
    if not row:
        pytest.skip('no ended semester')
    body = _payload(data, data['free'][:2], semesterid=row['semesterid'])
    r = admin.post('/admin/settings/merge_groups', json=body)
    assert r.status_code == 400 and any('read-only' in e for e in r.get_json()['errors'])


def test_settings_page_renders_the_merge_group_ui_without_the_pair_model(admin, tx):
    html = admin.get('/admin/settings').get_data(as_text=True)
    for anchor in ('mgGroupList', 'mgCreateBtn', 'modalMergeGroup', 'modalMergeImpact', 'mgSectionPicker',
                   'mgSubjectPicker', 'tog-merge', 'mergeLoadPolicyBody'):
        assert f'id="{anchor}"' in html, anchor
    # P7: the legacy configuration, migration tools and per-group meeting editor are gone.
    for gone in ('merge-section-pairs-container', 'msScopePicker', 'modalDeleteSectionPair',
                 'merge-sections-data', 'addSectionPair(', 'Leave empty to keep the legacy default',
                 'mgLegacyPanel', 'mgMigrationPanel', 'mgModelBanner', 'mgMeetingRows', 'MULTIPLE_FACULTY'):
        assert gone not in html, gone


# ── Migration previews ──────────────────────────────────────────────────────────

def test_discovery_and_dry_run_are_read_only(admin, tx, data):
    before = _count_groups(tx, data['sem'])
    d = admin.get(f"/admin/settings/merge_groups/discovery?semesterid={data['sem']}").get_json()
    assert d['success'] and isinstance(d['candidates'], list)
    for c in d['candidates']:
        assert {'errors', 'issues', 'state', 'members', 'meetings'} <= set(c)
    dr = admin.get(f"/admin/settings/merge_groups/dry_run?semesterid={data['sem']}").get_json()
    assert dr['success'] and dr['model'] in ('legacy', 'groups')
    assert {'new_conflicts', 'diverged', 'unscheduled'} <= set(dr['summary'])
    assert _count_groups(tx, data['sem']) == before


def test_discovery_confirm_creates_migrated_groups(admin, tx, data):
    cand = _payload(data, data['free'][:2], name='Migrated group')
    r = admin.post('/admin/settings/merge_groups/discovery/confirm', json={'candidates': [cand]})
    body = r.get_json()
    assert r.status_code == 200 and body['created'] == 1, body
    cur = _cur(tx)
    cur.execute("SELECT origin FROM merge_group WHERE groupname = 'Migrated group'")
    assert cur.fetchone()['origin'] == 'migrated'


# ── Settings endpoint: internal switch + frozen legacy keys ─────────────────────

def test_hard_constraint_endpoint_never_exposes_or_writes_the_switch_or_legacy_keys(admin, tx, data):
    cur = _cur(tx)
    mg.set_merge_model(cur, 'legacy')
    cur.execute("SELECT config_key, config_value FROM scheduler_config WHERE config_key = ANY(%s)",
                (list(mg.FROZEN_CONFIG_KEYS),))
    before = {r['config_key']: r['config_value'] for r in cur.fetchall()}
    tx.handle().commit()
    got = admin.get('/admin/settings/hard_constraints').get_json()
    assert mg.MODEL_KEY not in got
    r = admin.post('/admin/settings/hard_constraints', json={
        mg.MODEL_KEY: 'groups', 'hc_merge_scope': 'all_subjects',
        'hc_merge_scope_subjects': '["GEED 001"]', 'hc_merge_section_pairs': '[["A-1","B-1"]]',
        'hc_merge_enabled': got.get('hc_merge_enabled', 1)})
    assert r.get_json()['success']
    cur.execute("SELECT config_key, config_value FROM scheduler_config WHERE config_key = ANY(%s)",
                (list(mg.FROZEN_CONFIG_KEYS),))
    after = {r['config_key']: r['config_value'] for r in cur.fetchall()}
    assert after == before
    assert mg.get_merge_model(cur) == 'legacy'


def test_set_merge_model_rejects_unknown_values(tx):
    with pytest.raises(ValueError):
        mg.set_merge_model(_cur(tx), 'pairs')


# ── Equivalence override note is optional (audit who/when still required) ───────

def test_database_accepts_an_override_without_a_note_but_still_requires_its_audit(tx):
    import psycopg2
    cur = _cur(tx)
    mg.ensure_schema(cur)
    cur.execute(mg.schema_sql())        # the startup migration (idempotent constraint upgrade)
    cur.execute("SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                "WHERE conrelid = 'public.merge_group_member'::regclass AND conname = 'chk_mgm_override_audit'")
    d = cur.fetchone()['d']
    assert 'equivalence_note' not in d and 'override_by' in d and 'override_at' in d
    cur.execute("SELECT semesterid FROM semester ORDER BY semesterid LIMIT 1")
    sem = cur.fetchone()['semesterid']
    cur.execute("SELECT sc.sectionid, sc.curriculumsubjectid FROM schedule sc ORDER BY sc.scheduleid LIMIT 1")
    m = cur.fetchone()
    if not m:
        pytest.skip('no schedule row to borrow a section/subject from')
    cur.execute("INSERT INTO merge_group (groupname, semesterid, ref_curriculumsubjectid, faculty_mode, is_active) "
                "VALUES ('p1 note optional', %s, %s, 'MULTIPLE_FACULTY', FALSE) RETURNING mergegroupid",
                (sem, m['curriculumsubjectid']))
    gid = cur.fetchone()['mergegroupid']
    cur.execute("INSERT INTO merge_group_member (mergegroupid, sectionid, curriculumsubjectid, equivalence_basis, "
                "equivalence_note, override_by, override_at, is_active) "
                "VALUES (%s, %s, %s, 'admin_override', NULL, 'tester', NOW(), FALSE)",
                (gid, m['sectionid'], m['curriculumsubjectid']))
    cur.execute("SAVEPOINT no_audit")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("UPDATE merge_group_member SET override_by = NULL WHERE mergegroupid = %s", (gid,))
    cur.execute("ROLLBACK TO SAVEPOINT no_audit")
