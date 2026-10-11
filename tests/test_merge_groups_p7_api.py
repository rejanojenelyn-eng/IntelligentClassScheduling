"""HC16 P7 — "allowed" merging against the real database.

A class placed EXACTLY on another section's equivalent class (same day, start, end and
room) with the same faculty (or TBA on either side) is a merge: Save Draft / Publish
answer MERGE_CONFIRMATION_REQUIRED first, and only a confirmed resubmission records it
as a Merge Group meeting (origin 'editor'). Settings "merge sets" pre-register sections
as Merge Groups without meetings. Every test runs in ONE always-rolled-back transaction.
"""
import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import merge_groups as mg
from conftest import requires_db
from test_merge_groups_p2_api import _context, _make_group, _row, head, tx, world  # noqa: F401
from test_merge_groups_p5_api import _publish

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


def _groups(tx, w):
    cur = _cur(tx)
    return mg.load_groups(cur, w['cfg'], semester_ids=[w['sem']['semesterid']])


def _groups_of(tx, w):
    sections = {w['a'][0], w['b'][0]}
    return [g for g in _groups(tx, w) if sections & {m['sectionid'] for m in g['members']}]


def _save(head, w, rows, **kw):
    return head.post('/api/schedule/save-draft', json=dict({'context': _context(w), 'schedule_data': rows}, **kw))


def _draft_count(tx, w):
    cur = _cur(tx)
    cur.execute("SELECT COUNT(*) AS n FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid "
                "WHERE s.sectionid = %s AND s.semesterid = %s AND sv.status = 'Draft'",
                (w['a'][0], w['sem']['semesterid']))
    return cur.fetchone()['n']


# ── Save Draft: notice first, record only when confirmed ────────────────────────

def test_save_draft_on_another_sections_class_asks_first_then_records_the_merge(head, tx, world):
    fac = world['facs'][0]
    _publish(tx, world, 'b', fac)
    drafts = _draft_count(tx, world)
    r = _save(head, world, [_row(world, fac=fac)])
    body = r.get_json()
    assert r.status_code == 409 and body['code'] == 'MERGE_CONFIRMATION_REQUIRED', body
    (m,) = body['merges']
    assert m['other_section_id'] == world['b'][0] and m['section_id'] == world['a'][0]
    assert (m['day'], m['start'], m['end'], m['roomid']) == (world['day'], world['start'], world['end'],
                                                             world['room']['roomid'])
    assert m['other_status'] == 'Published' and m['faculty_id'] == fac and body['notice']
    assert _groups_of(tx, world) == [] and _draft_count(tx, world) == drafts      # nothing written yet

    ok = _save(head, world, [_row(world, fac=fac)], confirm_merges=True).get_json()
    assert ok.get('success') is True, ok
    assert ok['merged'] and world['sec'][world['b'][0]]['label'] in ok['merged'][0]
    (g,) = _groups_of(tx, world)
    assert g['origin'] == 'editor' and g['faculty_mode'] == mg.SAME_FACULTY and g['employeenumber'] is None
    assert {m['sectionid'] for m in g['members']} == {world['a'][0], world['b'][0]}
    (mt,) = g['meetings']
    assert (mt['daydesc'], mt['starttimeid'], mt['endtimeid'], mt['roomid']) == \
        (world['day'], world['stid'], world['etid'], world['room']['roomid'])
    # From now on the two classes ARE one merged class: no second notice, no HC16 issue.
    again = _save(head, world, [_row(world, fac=fac)]).get_json()
    assert again.get('success') is True and again['merged'] == [], again


def test_tba_on_either_side_still_merges(head, tx, world):
    _publish(tx, world, 'b', world['facs'][0])
    body = _save(head, world, [_row(world, fac='TBA')]).get_json()
    assert body['code'] == 'MERGE_CONFIRMATION_REQUIRED' and body['merges'][0]['faculty_id'] == world['facs'][0]


@pytest.mark.parametrize('case', ['other_faculty', 'other_room'])
def test_different_faculty_or_slot_is_never_a_merge(head, tx, world, case):
    _publish(tx, world, 'b', world['facs'][0])
    if case == 'other_faculty':
        row = _row(world, fac=world['facs'][1])
    else:
        other = next(r for r in mg.load_rooms(_cur(tx)).values()
                     if r['roomid'] != world['room']['roomid'] and r['roomtype'] == 'Lecture')
        row = _row(world, room=other['roomid'], fac=world['facs'][0])
    body = _save(head, world, [row]).get_json()
    assert body.get('success') is True and body['merged'] == [], body     # saved as an ordinary class
    assert _groups_of(tx, world) == []


def test_confirmed_merge_extends_the_sections_existing_allowance(head, tx, world):
    fac = world['facs'][0]
    gid = _make_group(tx, world, meetings=False, mode='SAME_FACULTY')['mergegroupid']   # allowed in Settings
    _publish(tx, world, 'b', fac)
    body = _save(head, world, [_row(world, fac=fac)]).get_json()
    assert body['code'] == 'MERGE_CONFIRMATION_REQUIRED' and body['merges'][0]['group'] == 'P2 merged class'
    assert _save(head, world, [_row(world, fac=fac)], confirm_merges=True).get_json()['success'] is True
    (g,) = _groups_of(tx, world)
    assert g['mergegroupid'] == gid and len(g['meetings']) == 1 and g['origin'] == 'admin'


def test_merge_with_a_draft_only_section_is_detected_too(head, tx, world):
    _publish(tx, world, 'b', world['facs'][0], status='Draft')
    body = _save(head, world, [_row(world, fac=world['facs'][0])]).get_json()
    assert body['code'] == 'MERGE_CONFIRMATION_REQUIRED' and body['merges'][0]['other_status'] == 'Draft'


# ── Publish: same notice ────────────────────────────────────────────────────────

def test_publish_asks_before_merging_and_records_on_confirmation(head, tx, world):
    fac = world['facs'][0]
    _publish(tx, world, 'b', fac)
    ctx = dict(_context(world), sectionId=world['a'][0])
    r = head.post('/api/schedule/approve', json={'context': ctx, 'schedule_data': [_row(world, fac=fac)]})
    assert r.status_code == 409 and r.get_json()['code'] == 'MERGE_CONFIRMATION_REQUIRED', r.get_json()
    assert _groups_of(tx, world) == []
    r = head.post('/api/schedule/approve', json={'context': ctx, 'schedule_data': [_row(world, fac=fac)],
                                                  'confirm_merges': True})
    body = r.get_json()
    assert body.get('code') != 'MERGE_CONFIRMATION_REQUIRED', body
    (g,) = _groups_of(tx, world)
    assert g['origin'] == 'editor' and len(g['meetings']) == 1
    # the merged class never counts as a room/faculty conflict between the two sections
    assert not any(v.get('rule') in ('HC10', 'HC11') for v in body.get('violations') or []), body


# ── Settings merge sets ─────────────────────────────────────────────────────────

def test_common_subjects_and_merge_set_create_allowances_without_meetings(admin, tx, world):
    sem = world['sem']['semesterid']
    ids = f"{world['a'][0]},{world['b'][0]}"
    subs = admin.get(f'/admin/settings/merge_groups/common_subjects?semesterid={sem}&section_ids={ids}').get_json()
    assert subs['success'] and any(s['key'] == world['a'][1] for s in subs['subjects'])
    secs = admin.get(f'/admin/settings/merge_groups/sections?semesterid={sem}').get_json()
    assert {world['a'][0], world['b'][0]} <= {s['sectionid'] for s in secs['sections']}

    r = admin.post('/admin/settings/merge_groups/sets', json={
        'semesterid': sem, 'section_ids': [world['a'][0], world['b'][0]], 'subject_keys': [world['a'][1]]})
    body = r.get_json()
    assert r.status_code == 201 and len(body['created']) == 1, body
    (g,) = _groups_of(tx, world)
    assert g['meetings'] == [] and g['origin'] == 'admin' and g['faculty_mode'] == mg.SAME_FACULTY
    # Allowing again changes nothing.
    again = admin.post('/admin/settings/merge_groups/sets', json={
        'semesterid': sem, 'section_ids': [world['a'][0], world['b'][0]], 'subject_keys': [world['a'][1]]}).get_json()
    assert again['created'] == [] and again['extended'] == []
    # Unmerging one of two sections removes the allowance entirely.
    gone = admin.delete(f"/admin/settings/merge_groups/{g['mergegroupid']}/members/{world['b'][0]}").get_json()
    assert gone['success'] and gone['deleted_group'] is True and _groups_of(tx, world) == []


def test_merge_set_needs_two_sections(admin, tx, world):
    r = admin.post('/admin/settings/merge_groups/sets', json={
        'semesterid': world['sem']['semesterid'], 'section_ids': [world['a'][0]]})
    assert r.status_code == 400 and 'at least 2' in r.get_json()['error']
