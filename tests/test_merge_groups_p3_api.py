"""HC17 under the group model — P3 against the real database (always rolled back).

Policy -> Merge Group mappings (explicit, unambiguous, also enforced by database
triggers), confirm-only suggestions, and the load surfaces — Faculty Load tab,
faculty portal and the HC9 existing-load batch — agreeing on one merged event.
"""
import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import faculty_load
import merge_groups as mg
from conftest import requires_db
from test_merge_groups_p2_api import _make_group, head, tx, world  # noqa: F401 (fixtures)

pytestmark = requires_db


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


@pytest.fixture
def admin(tx):
    cur = _cur(tx)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = 'Admin' ORDER BY userid LIMIT 1")
    acct = cur.fetchone()
    if not acct:
        pytest.skip('no active Admin account')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Admin', username=acct['username'])
    return c


def _policy(tx, lo, hi, code, name):
    cur = _cur(tx)
    mg.ensure_hc17_schema(cur)
    cur.execute("INSERT INTO merge_load_policy (subjectcode, min_sections, max_sections, policyname) "
                "VALUES (%s, %s, %s, %s) RETURNING policyid", (code, lo, hi, name))
    pid = cur.fetchone()['policyid']
    tx.handle().commit()
    return pid


def _second_group(tx, w):
    """An inactive second group over the same sections (allowed while inactive)."""
    cur = _cur(tx)
    body = {'groupname': 'P3 second group', 'semesterid': w['sem']['semesterid'],
            'ref_curriculumsubjectid': w['a'][1], 'faculty_mode': 'MULTIPLE_FACULTY', 'is_active': False,
            'members': [{'sectionid': w['a'][0], 'curriculumsubjectid': w['a'][1]},
                        {'sectionid': w['b'][0], 'curriculumsubjectid': w['b'][1]}], 'meetings': []}
    out = mg.save_group(cur, body, w['cfg'], username='p3-test', confirm_impact=True)
    assert out['ok'], out
    tx.handle().commit()
    return out['mergegroupid']


# ── Mapping, ambiguity, suggestions ─────────────────────────────────────────────

def test_one_policy_maps_to_many_groups_and_overlaps_are_rejected(admin, tx, world):
    g1 = _make_group(tx, world)['mergegroupid']
    g2 = _second_group(tx, world)
    # distinct legacy subject codes, so only the GROUP-mapping ambiguity rule is exercised
    p23 = _policy(tx, 2, 3, 'P3TEST-A', 'Shared 2-3')
    p34 = _policy(tx, 3, 4, 'P3TEST-B', 'Shared 3-4')
    p45 = _policy(tx, 4, 5, 'P3TEST-C', 'Shared 4-5')
    ok = admin.put(f'/admin/settings/merge_load_policies/{p23}/groups', json={'mergegroupids': [g1, g2]})
    assert ok.status_code == 200, ok.get_json()
    clash = admin.put(f'/admin/settings/merge_load_policies/{p34}/groups', json={'mergegroupids': [g1]})
    assert clash.status_code == 409 and clash.get_json()['conflicts']
    assert admin.put(f'/admin/settings/merge_load_policies/{p45}/groups',
                     json={'mergegroupids': [g1]}).status_code == 200
    # widening a mapped policy into an overlap is rejected too
    wider = admin.put(f'/admin/settings/merge_load_policies/{p45}',
                      json={'subjectcode': 'P3TEST-C', 'min_sections': 3, 'max_sections': 5})
    assert wider.status_code == 409 and wider.get_json()['conflicts']
    listing = admin.get('/admin/settings/merge_load_policies').get_json()
    mapped = {p['policyid']: {g['mergegroupid'] for g in p['groups']} for p in listing['policies']}
    assert mapped[p23] == {g1, g2} and mapped[p45] == {g1} and mapped[p34] == set()


def test_database_triggers_reject_ambiguous_mappings(tx, world):
    g1 = _make_group(tx, world)['mergegroupid']
    p23 = _policy(tx, 2, 3, world['code'], 'A')
    p34 = _policy(tx, 3, 4, world['code'], 'B')
    cur = _cur(tx)
    cur.execute("INSERT INTO merge_load_policy_group (policyid, mergegroupid) VALUES (%s, %s)", (p23, g1))
    cur.execute('SAVEPOINT p3_trg')
    with pytest.raises(psycopg2.errors.ExclusionViolation):
        cur.execute("INSERT INTO merge_load_policy_group (policyid, mergegroupid) VALUES (%s, %s)", (p34, g1))
    cur.execute('ROLLBACK TO SAVEPOINT p3_trg')
    p56 = _policy(tx, 5, 6, world['code'], 'C')
    cur.execute("INSERT INTO merge_load_policy_group (policyid, mergegroupid) VALUES (%s, %s)", (p56, g1))
    cur.execute('SAVEPOINT p3_trg2')
    with pytest.raises(psycopg2.errors.ExclusionViolation):
        cur.execute("UPDATE merge_load_policy SET min_sections = 3 WHERE policyid = %s", (p56,))
    cur.execute('ROLLBACK TO SAVEPOINT p3_trg2')


def test_suggestions_are_only_suggestions(admin, tx, world):
    g1 = _make_group(tx, world)['mergegroupid']
    pid = _policy(tx, 2, 3, world['code'], 'Suggest me')
    body = admin.get(f'/admin/settings/merge_load_policies/{pid}/groups').get_json()
    mine = next(g for g in body['groups'] if g['mergegroupid'] == g1)
    assert mine['suggested'] is True and mine['mapped'] is False
    cur = _cur(tx)
    cur.execute("SELECT COUNT(*) AS n FROM merge_load_policy_group WHERE policyid = %s", (pid,))
    assert cur.fetchone()['n'] == 0                               # nothing applied automatically


# ── All load surfaces agree on one merged event ─────────────────────────────────

def _publish(tx, w, which, fac):
    cur = _cur(tx)
    sec, csid = w[which]
    sid = app_module._find_or_create_schedule(cur, csid, sec, w['sem']['semesterid'], fac, 'Published')
    cur.execute("INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status, "
                "employeenumber) VALUES (%s, 9001, 'Published', 'manual_editor', 'Published', %s) "
                "RETURNING versionid", (sid, fac))
    vid = cur.fetchone()['versionid']
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, %s)", (vid, w['day'], w['stid'], w['etid'], w['room']['roomid']))
    tx.handle().commit()


def _surfaces(head, tx, w, fac):
    ay, sem = w['sem']['academicyearid'], w['sem']['semestertype']
    ta = head.get(f'/api/faculty/teaching_assignments?emp_num={fac}&ay_id={ay}&sem={sem}').get_json()
    assert ta['success'], ta
    portal, err = app_module._my_teaching_load_payload(_cur(tx), fac, ay, sem, 'official')
    assert err is None, err
    batch = faculty_load.get_faculty_load_batch(_cur(tx), ay, sem)
    return {'tab_total': round(float(ta['total_teaching_hours']), 2),
            'tab_buckets': round(float(ta['buckets']['used']), 2),
            'portal': round(float(portal['totals']['totalUnits']), 2),
            'hc9_existing': round(faculty_load.load_total(batch.get(fac)), 2),
            'rows': ta['sessions']}


def _hours(w):
    h1, m1 = map(int, w['start'].split(':'))
    h2, m2 = map(int, w['end'].split(':'))
    return ((h2 * 60 + m2) - (h1 * 60 + m1)) / 60


def test_group_mode_counts_each_section_on_every_surface_without_a_policy(head, tx, world):
    # P7 default: a merged class with no HC17 policy adds its load per section.
    fac = world['facs'][0]
    _make_group(tx, world)
    before = _surfaces(head, tx, world, fac)
    _publish(tx, world, 'a', fac)
    _publish(tx, world, 'b', fac)
    after = _surfaces(head, tx, world, fac)
    two = round(2 * _hours(world), 2)
    for k in ('tab_total', 'tab_buckets', 'portal', 'hc9_existing'):
        assert round(after[k] - before[k], 2) == two, (k, before[k], after[k])
    assert not any(r.get('merge_group') == 'P2 merged class' for r in after['rows'])


def test_group_mode_counts_the_merged_event_once_on_every_surface_under_a_policy(head, tx, world):
    fac = world['facs'][0]
    gid = _make_group(tx, world)['mergegroupid']
    pid = _policy(tx, 2, 2, world['subject']['subjectcode'], 'Shared once')
    cur = _cur(tx)
    cur.execute("INSERT INTO merge_load_policy_group (policyid, mergegroupid) VALUES (%s, %s)", (pid, gid))
    tx.handle().commit()
    before = _surfaces(head, tx, world, fac)
    _publish(tx, world, 'a', fac)
    _publish(tx, world, 'b', fac)
    after = _surfaces(head, tx, world, fac)
    one = round(_hours(world), 2)
    for k in ('tab_total', 'tab_buckets', 'portal', 'hc9_existing'):
        assert round(after[k] - before[k], 2) == one, (k, before[k], after[k])
    merged = [r for r in after['rows'] if r.get('merge_group') == 'P2 merged class']
    assert len(merged) == 1 and merged[0]['merged_sections'] == 2


def test_legacy_mode_load_surfaces_are_unchanged(head, tx, world):
    fac = world['facs'][0]
    _make_group(tx, world)
    mg.set_merge_model(_cur(tx), 'legacy')
    tx.handle().commit()
    before = _surfaces(head, tx, world, fac)
    _publish(tx, world, 'a', fac)
    _publish(tx, world, 'b', fac)
    after = _surfaces(head, tx, world, fac)
    # the test subject is outside the legacy NSTP-only scope: legacy never merges it
    assert round(after['tab_total'] - before['tab_total'], 2) == round(2 * _hours(world), 2)
    assert not any(r.get('merge_group') for r in after['rows'])


def test_live_model_and_policies_are_untouched_outside_the_transaction():
    from database import query_db
    row = query_db("SELECT config_value FROM scheduler_config WHERE config_key = %s", (mg.MODEL_KEY,), one=True)
    assert row is None or row['config_value'] in ('legacy', 'groups')    # P7 cutover: groups
    exists = query_db("SELECT to_regclass('public.merge_load_policy_group') IS NOT NULL AS ok", one=True)
    if exists['ok']:
        assert query_db("SELECT COUNT(*) AS n FROM merge_load_policy_group mlpg "
                        "JOIN merge_group mg ON mg.mergegroupid = mlpg.mergegroupid WHERE mg.created_by LIKE %s",
                        ('%-test',), one=True)['n'] == 0
