"""
Constraint-fix Phase 6 (independent part): Official Publish validates the
submitted rows TOGETHER WITH the same section's carried-forward rows for
HC10/HC11/HC12, and the incomplete-Draft Publish gate is scoped to the section.

HC6 is intentionally untouched here (pending the 15:00-18:00 block decision).
"""
from datetime import time
from pathlib import Path

import app as app_module

APP_SRC = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
CFG = {'hc_merge_enabled': 1, 'hc_merge_scope': 'nstp_only'}


def _sub(code='IT101', fac='F1', room=1, day='Monday', start=time(9, 0), end=time(10, 30)):
    return {'subject_code': code, 'faculty_id': fac, 'room_id': room, 'day': day,
            'days_list': [day], 'start_time': start, 'end_time': end}


def _carried(code='IT102', fac='F2', room=2, days=('Monday',), start='09:00:00', end='10:30:00'):
    # Shape produced by _group_carry_forward_sessions(_fetch_section_sessions(...)).
    return {'subjectcode': code, 'employeenumber': fac, 'roomid': room, 'daydesc': days[0],
            'days_list': list(days), 'start_time': start, 'end_time': end, 'status': 'Published'}


def _rules(submitted, carried):
    return sorted(v['rule'] for v in app_module._publish_carried_forward_conflicts(submitted, carried, CFG))


def test_new_subject_overlapping_an_untouched_subject_of_the_same_section_is_hc12():
    assert _rules([_sub()], [_carried()]) == ['HC12']


def test_same_faculty_and_same_room_with_carried_row_are_hc10_and_hc11():
    assert _rules([_sub()], [_carried(fac='F1', room=1)]) == ['HC10', 'HC11', 'HC12']


def test_adjacent_blocks_do_not_overlap():
    assert _rules([_sub(start=time(7, 30), end=time(9, 0))],
                  [_carried(start='09:00:00', end='10:30:00')]) == []


def test_multi_day_carried_row_is_checked_on_every_meeting_day():
    assert _rules([_sub(day='Thursday')], [_carried(days=('Monday', 'Thursday'))]) == ['HC12']
    assert _rules([_sub(day='Friday')], [_carried(days=('Monday', 'Thursday'))]) == []


def test_only_submitted_vs_carried_pairs_are_reported():
    # submitted-vs-submitted is the main CSP pass's job; carried-vs-carried is pre-existing.
    submitted = [_sub(), _sub(code='IT103', fac='F3', room=3)]
    carried = [_carried(code='IT201', fac='F9', room=9, days=('Tuesday',)),
               _carried(code='IT202', fac='F8', room=8, days=('Tuesday',))]
    assert app_module._publish_carried_forward_conflicts(submitted, carried, CFG) == []


def test_shared_hc10_exemption_applies_but_hc12_never_does():
    # NSTP/OU shared faculty (Phase 3 rule) -> no HC10; same section -> HC12 remains.
    assert _rules([_sub(code='NSTP101', room=1)],
                  [_carried(code='NSTP102', fac='F1', room=2)]) == ['HC12']


def test_violations_are_labelled_and_identify_both_subjects():
    v = app_module._publish_carried_forward_conflicts([_sub()], [_carried()], CFG)[0]
    assert v['type'] == 'Conflict: Section Schedule'
    assert 'IT101' in v['subject'] and 'IT102' in v['subject']


def test_nothing_to_compare():
    assert app_module._publish_carried_forward_conflicts([], [_carried()], CFG) == []
    assert app_module._publish_carried_forward_conflicts([_sub()], [], CFG) == []


# ── wiring in api_approve_schedule ──────────────────────────────────────────

APPROVE = APP_SRC[APP_SRC.index('def api_approve_schedule'):APP_SRC.index('def api_delete_draft')]


def test_publish_runs_the_carried_forward_check_for_the_known_section_only():
    call = APPROVE[APPROVE.index('if publish_gate_on and _ctx_section_id:'):]
    call = call[:call.index('# HC15 cross-schedule validation')]
    assert '_publish_carried_forward_conflicts(' in call
    assert 'other_sessions + published_baseline' in call
    assert 'conn.rollback()' in call and "'violations': carried_conflicts" in call and '), 400' in call
    # runs before the snapshot is written
    assert APPROVE.index('_publish_carried_forward_conflicts(') < APPROVE.index('_insert_batch(cur, complete_snapshot')


def test_carried_rows_exclude_submitted_subjects_so_nothing_self_conflicts():
    fetch = APPROVE[APPROVE.index('other_sessions = _group_carry_forward_sessions('):]
    fetch = fetch[:fetch.index(')\n        )')]
    assert 'exclude_codes=submitted_codes' in fetch and 'section_id=_ctx_section_id' in fetch


def test_incomplete_draft_gate_is_scoped_to_the_section_and_published_subjects():
    gate = APPROVE[APPROVE.index('AND sv.is_incomplete = TRUE') - 900:APPROVE.index('_incomplete_rows = cur.fetchall()')]
    assert 's.sectionid = %s::int' in gate
    # Another subject's unfinished Draft is not part of this Publish and must not block it.
    assert 'UPPER(cs.subjectcode) = ANY(%s)' in gate
    assert '(program, year_level, sem_id, _ctx_section_id, _ctx_section_id, submitted_codes)' in gate
