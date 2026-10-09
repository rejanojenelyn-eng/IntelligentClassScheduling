"""HC16 group model — P5 Manual Editor UX (P7 semantics), driving the REAL editor JavaScript
(manualEditor.acad2.js + manualEditor.mergeGroups.js + the template's inline scripts)
in Node through tests/js/merge_groups_editor_harness.js.

These verify behavior and rendered markup only. They are NOT a visual/browser check.
"""
import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / 'js' / 'merge_groups_editor_harness.js'
NODE = shutil.which('node')


@lru_cache(maxsize=None)
def _all():
    if not NODE:
        pytest.skip('node is not installed')
    out = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True, encoding='utf-8', timeout=180)
    data = json.loads(out.stdout)
    assert 'harness_error' not in data, data
    return data


def run(name):
    d = _all()[name]
    assert 'scenario_error' not in d, d['scenario_error']
    return d


# ── Legacy vs group model ───────────────────────────────────────────────────────

def test_legacy_model_keeps_legacy_merge_behavior_and_never_asks_the_server():
    d = run('legacy_mode')
    assert d['errors'] == [] and d['model'] == 'legacy' and d['nstp'] == 'flexible'   # NSTP flexible rule kept
    assert d['active'] is False and d['refreshed'] is None and d['fetched_merge_context'] is False
    assert d['legacy_viol_type'] == 'Merged-Class Validity' and d['legacy_viol_html'] == ''
    assert d['merge_candidate'] is False                    # P7 merge candidates are group-model only


def test_group_model_turns_every_legacy_merge_rule_off():
    d = run('groups_disable_legacy')
    assert d['errors'] == [] and d['model'] == 'groups' and d['active'] is True
    assert d['nstp'] == 'none' and d['eligible'] is False


def test_local_scheduler_is_not_changed_in_p5():
    d = run('local_mode_is_untouched')
    assert d == {'active': False, 'refreshed': None, 'fetched': False}


# ── P7: merged subjects are shown, never locked ─────────────────────────────────

def test_editor_posts_its_current_slices_and_shows_the_merge_badge():
    d = run('render_states')
    assert d['errors'] == []
    assert d['posted_section'] == '9' and d['posted_subject'] == 'NSTP 001'
    assert d['posted_rows'][0] == ['1', 'Sunday', '09:00 AM', '5', 'F1']
    assert d['posted_keys'] == ['day', 'end_time', 'faculty_id', 'key', 'room_id', 'start_time', 'subject_code']
    assert d['badge_text'] == 'MERGED · with BSCS 1-A' and d['badge_hidden'] is False
    tip = dict(d['tooltip'])
    assert set(tip) == {'Merge Group', 'Subject', 'Sections', 'Merged slots'}
    assert tip['Sections'] == 'BSIT 1-A, BSCS 1-A' and 'R5' in tip['Merged slots'] and 'BSCS 1-A' in tip['Merged slots']
    assert '1:11' not in json.dumps(d['tooltip'])                      # no ids as primary text
    assert 'mg-tip' in d['badge_html']


def test_merged_slice_is_marked_but_stays_editable():
    d = run('render_states')
    r1 = d['r1']
    assert r1['locked'] is None and r1['ev'] == '1:11' and not any(r1['disabled']) and r1['cls'] is False
    assert 'MERGED with BSCS 1-A' in r1['note'] and 'RESET' not in d['banner']
    assert 'Moving a merged slice makes it a separate class again' in d['banner']


def test_separate_and_blank_slices_are_ordinary_and_add_stays_available():
    d = run('render_states')
    assert d['r2'] == {'locked': None, 'ev': '', 'sync': 'separate', 'note': ''}
    assert d['day_value_kept'] == 'Monday'
    assert d['r3_present'] is True and d['add_hidden'] == ''


def test_allowed_but_unmerged_subject_shows_only_the_can_merge_badge():
    d = run('not_merged_group')
    assert d['badge'] == 'CAN MERGE · with BSCS 1-A'
    assert d['banner'] == ''   # no notice under Time Allotment; the blue badge says it
    assert d['note'] == '' and d['locked'] is None and d['disabled'] is False


def test_no_edit_path_is_guarded_by_a_working_context():
    d = run('guards')
    assert d == {'block_add': False, 'fac_guard': False, 'row_locked': False, 'controlled': False,
                 'fac_locked': False, 'modals': [], 'reset_fn': 'undefined'}


def test_designated_faculty_mismatch_is_shown_but_faculty_stays_editable():
    d = run('designee_never_locks_faculty')
    assert 'requires its designated faculty Reyes, Ana' in d['note'] and d['wrapper_locked'] is False
    assert d['menu_opened'] == 'block' and d['sel_faculty'] == 'F1' and d['modals'] == []


def test_same_faculty_tba_shows_an_advisory_on_the_slice():
    d = run('same_faculty_tba_advisory')
    assert d['locked'] is False and d['menu_opened'] == 'block' and d['modals'] == []
    assert 'TBA keeps it incomplete' in d['note']


# ── Occupancy and placement ─────────────────────────────────────────────────────

def test_occupancy_exempts_only_the_same_event_of_another_section():
    d = run('occupancy_same_event')
    assert (d['with_key'], d['no_key'], d['legacy']) == (3, 4, 1)
    assert d['merge_flags'] == [False, False, False, False]          # faculty F2 is not the selected F1
    assert (d['same'], d['own_section'], d['other_key'], d['outsider']) == (True, False, False, False)


def test_same_subject_same_faculty_slot_stays_available_only_at_its_exact_time():
    d = run('occupancy_merge_candidate')
    assert d['same_faculty'] == [True] and d['other_tba'] == [True]
    assert d['exact_blocks'] is False and d['partial_blocks'] is True and d['longer_blocks'] is True
    assert d['other_faculty'] == [False] and d['other_subject'] == [False] and d['own_section'] == [False]


def test_placement_same_event_passes_outsider_blocks_and_no_merge_prompt():
    d = run('place_same_event_vs_outsider')
    assert d['same_event']['placed'] == 1 and d['same_event']['merge_event'] == '1:11'
    assert d['same_event']['modals'] == []
    assert d['outsider_same_subject']['placed'] == 0                  # other faculty: still a conflict
    assert d['outsider_same_subject']['modals'][0].startswith('CONFLICT Room conflict')
    assert not d['same_event']['merge_confirm'] and not d['outsider_same_subject']['merge_confirm']


def test_placing_exactly_on_a_same_subject_same_faculty_class_is_a_merge_not_a_conflict():
    d = run('place_merge_candidate')
    assert d['exact_same_faculty'] == {'placed': 1, 'modals': []}
    assert d['exact_other_tba'] == {'placed': 1, 'modals': []}
    for case in ('exact_other_faculty', 'partial_same_faculty', 'exact_other_subject'):
        assert d[case]['placed'] == 0 and d[case]['modals'][0].startswith('CONFLICT Room conflict'), case


def test_faculty_assignment_check_exempts_the_same_event_and_an_exact_merge():
    d = run('faculty_assignment_conflict')
    assert d['same_event'] == '' and 'already has' in d['outsider']
    assert d['merge_exact'] == ''
    assert 'already has' in d['merge_other_room'] and 'already has' in d['merge_other_time']


def test_save_draft_shows_the_merge_notice_and_resubmits_only_when_confirmed():
    d = run('save_draft_merge_notice')
    assert d['errors'] == [] and d['ok'] is True and d['bodies'] == [False, True]
    assert d['shown'][0] == ['NSTP 001: BSIT 1-A merges with BSCS 1-A']
    assert d['declined'] is False and d['declined_bodies'] == [False]    # declined: nothing resubmitted


# ── Conflict panel ──────────────────────────────────────────────────────────────

def test_conflict_card_shows_the_merged_class_consistency_category():
    d = run('conflict_card')
    assert d['type'] == 'Merged-Class Consistency' and d['other_rule'] == 'Room'
    for label in ('Group', 'Section', 'Expected slot', 'Actual slot', 'Faculty issue'):
        assert f'<span>{label}</span>' in d['html']
    assert 'NSTP Sunday Block' in d['html'] and 'Monday 7:30 AM' in d['html']
    assert 'Has F1; requires F2 (BSCS 1-A)' in d['fac_html']


# ── Robustness ──────────────────────────────────────────────────────────────────

def test_stale_responses_are_dropped_and_a_server_error_fails_closed():
    d = run('stale_and_error')
    assert d['calls'] == 2 and d['kept'] == 'NSTP 002'
    assert 'MERGE GROUP DETAILS UNAVAILABLE' in d['error_banner'] and 'RETRY' in d['error_banner']
    assert d['error_locked'] is True and d['error_controlled'] is True


def test_context_failure_disables_editing_save_and_publish():
    b = run('fail_closed_and_retry')['broken']
    assert b['broken'] is True and b['controlled'] is True
    assert b['row_locked'] == '1' and b['field_disabled'] is True and b['add_hidden'] == 'none'
    assert b['fac_locked'] is True and b['room_after'] == '7'
    assert 'MERGE GROUP DETAILS UNAVAILABLE' in b['banner'] and '_mgRetry()' in b['banner']
    assert 'editing disabled' in b['note']
    assert b['save_draft'] is False and b['publish'] is False
    assert b['writes'] == 0                                         # nothing reached Save Draft / Publish
    kinds = [m.split(':')[0] for m in b['modals']]
    assert kinds == ['Merge Group Details Unavailable'] * 6
    for action in ('save this Draft', 'publish', 'Cannot save:', 'save this slice', 'cannot be added',
                   'faculty cannot be changed'):
        assert any(action in m for m in b['modals']), action


def test_http_error_keeps_it_closed_and_a_successful_retry_reopens_everything():
    d = run('fail_closed_and_retry')
    assert d['errors'] == []
    assert d['still']['broken'] is True and 'boom' in d['still']['banner']
    r = d['recovered']
    assert r['broken'] is False and r['controlled'] is False and r['row_locked'] is None
    assert r['field_disabled'] is False and r['add_hidden'] == '' and r['fac_locked'] is False
    assert r['banner'] == '' and r['guard'] is False


def test_save_and_publish_wait_while_context_is_loading():
    d = run('loading_blocks_save')
    assert d['during'] is True and 'still loading' in d['msg'][0] and d['after'] is False


def test_legacy_mode_is_never_blocked_by_merge_context():
    d = run('legacy_never_blocks')
    assert d == {'broken': False, 'guard': False, 'controlled': False, 'fac': False, 'fetched': False}
