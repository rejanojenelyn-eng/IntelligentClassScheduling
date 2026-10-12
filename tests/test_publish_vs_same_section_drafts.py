"""Publish must not take a slot that a saved Draft of ANOTHER subject in the SAME
section already holds (same faculty / room / the section itself).

Before: the carried-forward check compared only this section's Published subjects
and the cross-schedule check counted only OTHER sections' Drafts, so a subject
could be published on top of a pending Draft of the same section — e.g. one faculty
in two classes at once — and the conflict surfaced only when that Draft was
published later. Real database, one always-rolled-back transaction.
"""
from psycopg2.extras import RealDictCursor

import app as app_module
from conftest import requires_db
from test_merge_groups_p2_api import _context, _row, head, tx, world  # noqa: F401

pytestmark = requires_db


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


def _other_subject_of_section(tx, w):
    """Another curriculum subject offered to section A (not the world's subject)."""
    sec = w['sec'][w['a'][0]]
    cur = _cur(tx)
    cur.execute("SELECT curriculumsubjectid, subjectcode FROM curriculumsubject "
                "WHERE curriculumsubjectid = ANY(%s) AND UPPER(subjectcode) <> UPPER(%s) "
                "ORDER BY curriculumsubjectid LIMIT 1",
                (list(sec['offered']), w['subject']['subjectcode']))
    row = cur.fetchone()
    if not row:
        import pytest
        pytest.skip('section has no second subject')
    return row


def _save_draft_of(tx, w, csid, fac, day):
    cur = _cur(tx)
    sid = app_module._find_or_create_schedule(cur, csid, w['a'][0], w['sem']['semesterid'], fac, 'Draft')
    cur.execute("INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status, "
                "employeenumber) VALUES (%s, 9001, 'Draft', 'manual_editor', 'Draft', %s) RETURNING versionid",
                (sid, fac))
    vid = cur.fetchone()['versionid']
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, NULL)", (vid, day, w['stid'], w['etid']))
    tx.handle().commit()


def _approve(head, w, fac):
    ctx = dict(_context(w), sectionId=w['a'][0])
    return head.post('/api/schedule/approve',
                     json={'context': ctx, 'schedule_data': [_row(w, fac=fac)], 'confirm_merges': True})


def test_publish_onto_a_same_section_draft_with_the_same_faculty_is_blocked(head, tx, world):
    fac = world['facs'][0]
    other = _other_subject_of_section(tx, world)
    _save_draft_of(tx, world, other['curriculumsubjectid'], fac, world['day'])

    r = _approve(head, world, fac)
    body = r.get_json()
    assert r.status_code == 400, body
    assert "saved Draft subjects" in body['error'], body
    rules = {v.get('rule') for v in body['violations']}
    assert 'HC10' in rules, body          # same faculty, same time
    assert 'HC12' in rules, body          # same section, same time
    assert any(other['subjectcode'].upper() in (v.get('subject') or '').upper()
               for v in body['violations']), body


def test_a_same_section_draft_on_another_day_does_not_block(head, tx, world):
    fac = world['facs'][0]
    other = _other_subject_of_section(tx, world)
    other_day = next(d for d in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
                     if d != world['day'])
    _save_draft_of(tx, world, other['curriculumsubjectid'], fac, other_day)

    body = _approve(head, world, fac).get_json()
    assert "saved Draft subjects" not in (body.get('error') or ''), body
