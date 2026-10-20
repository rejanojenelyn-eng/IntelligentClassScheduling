"""
HC17: a merged class takes its subject's load policy automatically. A merged class made
in the Manual Editor (e.g. NSTP 001 · BSAM-1 + BSEE-1) used to stay unmapped — the
policy listed "No groups assigned" and the shared faculty was credited once PER
SECTION. Now the policy whose subject matches (exact code, or the grouped 'NSTP' entry
for every NSTP/OU variant) is mapped as soon as the merged class exists, and a mapping
the admin removes on purpose is never put back. Runs inside a rolled-back transaction.
"""
import io
import contextlib
import uuid
from datetime import datetime

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import faculty_load
from conftest import requires_db


@pytest.fixture
def tx(monkeypatch):
    from _shared_tx import SharedTx, install
    t = SharedTx()
    install(monkeypatch, t)
    try:
        yield t
    finally:
        t.close()


def _client(tx, role):
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username FROM accounts WHERE isactive AND role = %s ORDER BY userid LIMIT 1", (role,))
    acct = cur.fetchone()
    if not acct:
        pytest.skip(f'no active {role} account')
    c = app_module.app.test_client()
    now = datetime.now().timestamp()
    with c.session_transaction() as s:
        s.update(loggedin=True, role=role, username=acct['username'], last_seen=now,
                 last_active=now, sid=uuid.uuid4().hex)
    return c


@pytest.fixture
def world(tx):
    """An NSTP merged class of two sections (same faculty, at its meeting) + the NSTP policy."""
    cur = tx.raw.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT g.mergegroupid, g.semesterid, mt.daydesc, mt.starttimeid, mt.endtimeid, mt.roomid
        FROM merge_group g
        JOIN curriculumsubject cs ON cs.curriculumsubjectid = g.ref_curriculumsubjectid
        JOIN merge_group_meeting mt ON mt.mergegroupid = g.mergegroupid
        JOIN semester sm ON sm.semesterid = g.semesterid AND sm.isactive
        WHERE g.is_active AND UPPER(cs.subjectcode) LIKE 'NSTP%%'
        ORDER BY g.mergegroupid LIMIT 1
    """)
    g = cur.fetchone()
    if not g:
        pytest.skip('no active NSTP merged class')
    cur.execute("SELECT sectionid, curriculumsubjectid FROM merge_group_member WHERE mergegroupid = %s AND is_active",
                (g['mergegroupid'],))
    members = cur.fetchall()
    cur.execute("DELETE FROM merge_load_policy_group WHERE mergegroupid = %s", (g['mergegroupid'],))
    app_module._ensure_hc17_optout_table(cur)
    cur.execute("DELETE FROM merge_load_policy_group_optout WHERE mergegroupid = %s", (g['mergegroupid'],))
    cur.execute("SELECT policyid FROM merge_load_policy WHERE UPPER(subjectcode) = 'NSTP' AND min_sections <= %s "
                "AND max_sections >= %s LIMIT 1", (len(members), len(members)))
    pol = cur.fetchone()
    if not pol:
        cur.execute("INSERT INTO merge_load_policy (subjectcode, min_sections, max_sections, policyname) "
                    "VALUES ('NSTP', 2, 10, 'NSTP') RETURNING policyid")
        pol = cur.fetchone()
    cur.execute("SELECT employeenumber FROM faculty WHERE employeestatus != 'Archive' ORDER BY employeenumber LIMIT 1")
    emp = cur.fetchone()['employeenumber']
    for m in members:   # the shared faculty teaches each member section's NSTP at the merged meeting
        cur.execute("INSERT INTO schedule (curriculumsubjectid, sectionid, employeenumber, semesterid) "
                    "VALUES (%s, %s, %s, %s) RETURNING scheduleid", (m['curriculumsubjectid'], m['sectionid'], emp, g['semesterid']))
        sid = cur.fetchone()['scheduleid']
        cur.execute("INSERT INTO schedule_version (scheduleid, version_number, status, source, employeenumber) "
                    "VALUES (%s, 1, 'Published', 'official', %s) RETURNING versionid", (sid, emp))
        vid = cur.fetchone()['versionid']
        cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                    "VALUES (%s, %s, %s, %s, %s)", (vid, g['daydesc'], g['starttimeid'], g['endtimeid'], g['roomid']))
    cur.execute("SELECT academicyearid, semestertype FROM semester WHERE semesterid = %s", (g['semesterid'],))
    sem = cur.fetchone()
    return dict(cur=cur, gid=g['mergegroupid'], sem_id=g['semesterid'], policyid=pol['policyid'], emp=emp,
                ay=sem['academicyearid'], term=sem['semestertype'], n=len(members))


def _load(w):
    cfg = app_module._load_cfg_for(w['ay'], w['term'], cur=w['cur'], sem_id=w['sem_id'])
    return faculty_load.load_total(faculty_load.get_faculty_load_batch(
        w['cur'], w['ay'], w['term'], config=cfg).get(w['emp']))


def _mapped(w):
    w['cur'].execute("SELECT 1 FROM merge_load_policy_group WHERE policyid = %s AND mergegroupid = %s",
                     (w['policyid'], w['gid']))
    return w['cur'].fetchone() is not None


@requires_db
def test_merged_class_is_mapped_by_subject_and_credited_once(tx, world):
    per_section = _load(world)
    assert not _mapped(world)
    assert app_module._auto_map_hc17_policies(world['cur'], group_ids=[world['gid']]) >= 1
    assert _mapped(world)
    assert _load(world) < per_section            # the shared NSTP now counts once, not per section


@requires_db
def test_settings_list_maps_existing_merged_classes(tx, world):
    with contextlib.redirect_stdout(io.StringIO()):
        body = _client(tx, 'Admin').get('/admin/settings/merge_load_policies').get_json()
    pol = next(p for p in body['policies'] if p['policyid'] == world['policyid'])
    assert world['gid'] in [g['mergegroupid'] for g in pol['groups']]


@requires_db
def test_a_mapping_the_admin_removed_is_not_put_back(tx, world):
    admin = _client(tx, 'Admin')
    app_module._auto_map_hc17_policies(world['cur'], group_ids=[world['gid']])
    world['cur'].execute("SELECT mergegroupid FROM merge_load_policy_group WHERE policyid = %s", (world['policyid'],))
    keep = [r['mergegroupid'] for r in world['cur'].fetchall() if r['mergegroupid'] != world['gid']]
    with contextlib.redirect_stdout(io.StringIO()):
        r = admin.put(f"/admin/settings/merge_load_policies/{world['policyid']}/groups",
                      json={'mergegroupids': keep}).get_json()
        admin.get('/admin/settings/merge_load_policies')
    assert r['success'] is True
    assert not _mapped(world)
    assert app_module._auto_map_hc17_policies(world['cur'], group_ids=[world['gid']]) == 0
