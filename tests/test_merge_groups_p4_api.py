"""HC16 group model — P4 real generation, mergedclass sync and evaluation against the
real database. Every test runs inside ONE always-rolled-back transaction
(tests/_shared_tx.py); group mode, Merge Groups and schedule rows exist only inside it.

These run the REAL production GA (app.scheduler_engine.generate_draft), so each
generation takes a while — kept to the few runs that prove the behavior.
"""
from datetime import time

import pytest
from psycopg2.extras import RealDictCursor

import app as app_module
import merge_groups as mg
from conftest import requires_db
from test_merge_groups_p2_api import _make_group, tx, world  # noqa: F401 (fixtures)

pytestmark = requires_db


def _cur(tx):
    return tx.raw.cursor(cursor_factory=RealDictCursor)


def _t(hm):
    h, m = map(int, hm.split(':'))
    return time(h, m)


def _generate(w, which, seed):
    """Generate one member section with ITS OWN curriculum year (members may come from
    different programs/curricula)."""
    sec = w['sec'][w[which][0]]
    from database import query_db
    cy = query_db("SELECT c.curriculumyear FROM curriculumsubject cs JOIN curriculum c "
                  "ON c.curriculumid = cs.curriculumid WHERE cs.curriculumsubjectid = %s",
                  (w[which][1],), one=True)['curriculumyear']
    return app_module.scheduler_engine.generate_draft(
        sec['programcode'], sec['yearlevel'], w['sem']['semestertype'], cy,
        acad_year_id=w['sem']['academicyearid'], seed=seed, section_id=w[which][0])


def _merged_genes(res, w):
    return [g for g in res.get('schedule_data') or []
            if mg.norm_code(g.get('subject_code')) == mg.norm_code(w['subject']['subjectcode'])]


def _publish(tx, w, which, fac, *, sectionid=None, csid=None, room=None):
    cur = _cur(tx)
    sec, cs = (sectionid, csid) if sectionid else w[which]
    sid = app_module._find_or_create_schedule(cur, cs, sec, w['sem']['semesterid'], fac, 'Published')
    cur.execute("INSERT INTO schedule_version (scheduleid, version_number, status, source, original_status, "
                "employeenumber) VALUES (%s, 9001, 'Published', 'manual_editor', 'Published', %s) "
                "RETURNING versionid", (sid, fac))
    vid = cur.fetchone()['versionid']
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, %s)",
                (vid, w['day'], w['stid'], w['etid'], room if room is not None else w['room']['roomid']))
    tx.handle().commit()


def _assert_pinned(res, w):
    genes = _merged_genes(res, w)
    assert genes, res.get('error')
    for g in genes:
        assert (g['day'], g['start_time'], g['end_time'], g['room_id']) == \
            (w['day'], _t(w['start']), _t(w['end']), w['room']['roomid']), g
        assert g.get('merge_group') == 'P2 merged class'


def _schedule_rows(tx):
    cur = _cur(tx)
    cur.execute("SELECT (SELECT COUNT(*) FROM schedule) s, (SELECT COUNT(*) FROM schedule_version) v, "
                "(SELECT COUNT(*) FROM schedule_sessions) ss, (SELECT COUNT(*) FROM merge_group) g, "
                "(SELECT COUNT(*) FROM merge_group_meeting) m")
    return dict(cur.fetchone())


# ── Generation pins the merged subject to the group meeting ─────────────────────

def test_generation_pins_merged_subject_and_writes_nothing(tx, world):
    _make_group(tx, world)
    before = _schedule_rows(tx)
    res = _generate(world, 'a', seed=7)
    assert res['result_status'] in ('COMPLETE_VALID', 'PARTIAL_VALID', 'INVALID_RESULT'), res.get('error')
    _assert_pinned(res, world)
    assert not any(v.get('rule') == 'HC16' and v.get('severity') != 'warning' for v in res['violations'])
    assert _schedule_rows(tx) == before                                   # no writes, no cross-section writes


def test_second_member_joins_the_published_event_but_an_outsider_still_conflicts(tx, world):
    fac = world['facs'][0]
    _make_group(tx, world)
    _publish(tx, world, 'a', fac)                                         # member A already Published
    res = _generate(world, 'b', seed=11)
    _assert_pinned(res, world)                                            # same slot as A — order independent
    code = world['subject']['subjectcode']
    room_conflicts = [v for v in res['violations'] if v.get('rule') == 'HC11' and code in str(v.get('subject'))]
    assert room_conflicts == []                                           # A's same-event row never blocks B

    # an outsider (a non-member section) now occupies the group room at that time
    cur = _cur(tx)
    cur.execute("SELECT sv.versionid FROM schedule_version sv JOIN schedule s ON s.scheduleid = sv.scheduleid "
                "WHERE sv.status = 'Published' AND s.semesterid = %s AND s.sectionid <> ALL(%s) "
                "ORDER BY sv.versionid LIMIT 1",
                (world['sem']['semesterid'], [world['a'][0], world['b'][0]]))
    other = cur.fetchone()
    if not other:
        pytest.skip('no other Published section to act as an outsider')
    cur.execute("INSERT INTO schedule_sessions (versionid, daydesc, starttimeid, endtimeid, roomid) "
                "VALUES (%s, %s, %s, %s, %s)",
                (other['versionid'], world['day'], world['stid'], world['etid'], world['room']['roomid']))
    tx.handle().commit()
    res = _generate(world, 'b', seed=11)
    _assert_pinned(res, world)                                            # the group room is NOT replaced
    assert [v for v in res['violations'] if v.get('rule') == 'HC11' and code in str(v.get('subject'))]


def test_allowed_but_unmerged_group_subject_is_generated_normally(tx, world):
    # P7: sections only allowed to merge (no merged slot yet) are generated like any subject.
    _make_group(tx, world, meetings=False)
    res = _generate(world, 'a', seed=7)
    genes = _merged_genes(res, world)
    assert genes and all(g.get('start_time') is not None for g in genes)
    assert not any("meeting configured" in r for g in genes for r in g.get('incomplete_reason') or [])


# ── mergedclass derived sync ────────────────────────────────────────────────────

def _mergedclass_sections(tx, w):
    cur = _cur(tx)
    cur.execute("SELECT mcs.sectionid, mc.employeenumber, mc.classname FROM mergedclass mc "
                "JOIN mergedclass_sections mcs ON mcs.mergedclassid = mc.mergedclassid "
                "WHERE mc.semesterid = %s AND mcs.sectionid = ANY(%s)",
                (w['sem']['semesterid'], [w['a'][0], w['b'][0]]))
    return cur.fetchall()


def test_group_mode_mergedclass_sync_derives_from_hc16_events_only(tx, world):
    fac = world['facs'][0]
    _make_group(tx, world)
    _publish(tx, world, 'a', fac)
    _publish(tx, world, 'b', fac)
    config_before = _schedule_rows(tx)
    app_module._sync_mergedclass_for_semester(_cur(tx), world['sem']['semesterid'])
    rows = _mergedclass_sections(tx, world)
    assert {r['sectionid'] for r in rows} == {world['a'][0], world['b'][0]}
    assert {r['employeenumber'] for r in rows} == {fac} and all('P2 merged class' in r['classname'] for r in rows)
    after = _schedule_rows(tx)
    assert (after['g'], after['m']) == (config_before['g'], config_before['m'])   # config untouched


def test_legacy_mergedclass_sync_is_unchanged(tx, world):
    fac = world['facs'][0]
    _make_group(tx, world)
    mg.set_merge_model(_cur(tx), 'legacy')
    tx.handle().commit()
    _publish(tx, world, 'a', fac)
    _publish(tx, world, 'b', fac)
    app_module._sync_mergedclass_for_semester(_cur(tx), world['sem']['semesterid'])
    # the test subject is outside the legacy NSTP-only scope: legacy never merges it
    assert _mergedclass_sections(tx, world) == []


# ── Generated/retrieved evaluation reports HC16 validity, weights unchanged ─────

def test_evaluation_reports_hc16_validity_without_a_score_weight(tx, world):
    # P7: an invalid merged class is now a faculty mismatch at the merged slot (being off
    # the slot is an ordinary class): designated faculty 0, this section has faculty 1.
    _make_group(tx, world, mode='SAME_FACULTY', designated=world['facs'][0])
    sec = world['sec'][world['a'][0]]
    row = {'subject_code': world['subject']['subjectcode'], 'class_type': 'Lecture',
           'faculty_id': world['facs'][1], 'room_id': world['room']['roomid'], 'room': world['room']['roomname'],
           'days_list': [world['day']],
           'day': world['day'], 'start_time': world['start'], 'end_time': world['end'], 'course': sec['programcode'],
           'lec_hours': world['subject']['lecturehours'], 'units': world['subject']['creditunits']}
    ev = app_module._compute_schedule_evaluation(
        [row], sec['programcode'], sec['yearlevel'], world['sem']['semestertype'],
        acad_year=world['sem']['academicyearid'], section_id=world['a'][0])
    flat = [v for vs in (ev.get('violationsBySubject') or {}).values() for v in vs]
    assert any(v.get('rule') == 'HC16' for v in flat) or ev['hardViolationCount'] >= 1
    assert ev['eligibleForApproval'] is False
    frozen = {'facultyConflictFree': ('HC10',), 'roomConflictFree': ('HC11',), 'sectionConflictFree': ('HC12',),
              'roomTypeSuitability': ('HC13', 'HC14'), 'subjectDayCompliance': ('HC5',),
              'schedulePairingDistribution': ('HC6', 'HC7'),
              'facultyLoadCompliance': ('HC1', 'HC2', 'HC3', 'HC4', 'HC8', 'HC9', 'HC17')}
    assert all(app_module._EVAL_CRITERION_RULES[k] == v for k, v in frozen.items())
    assert not any('HC16' in rules for rules in app_module._EVAL_CRITERION_RULES.values())


def test_live_model_and_groups_are_untouched_outside_the_transaction():
    from database import query_db
    row = query_db("SELECT config_value FROM scheduler_config WHERE config_key = %s", (mg.MODEL_KEY,), one=True)
    assert row is None or row['config_value'] in ('legacy', 'groups')   # P7: cut over to groups
    assert query_db("SELECT COUNT(*) AS n FROM merge_group WHERE created_by LIKE %s",
                    ('%-test',), one=True)['n'] == 0              # nothing a test created survives
