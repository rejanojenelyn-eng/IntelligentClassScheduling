"""Manual Editor dirty-state lifecycle and Published-slice removal (Phase 2 UI + Phase 5).

Runs the REAL manualEditor.acad2.js + manualScheduleEditor.html inline scripts under Node
(tests/js/manual_editor_state_harness.js) with a scripted fetch().

  Edit -> dirty. Successful Save / Publish -> clean. Failed Save / Publish -> still dirty.
  A Published slice is never deleted directly: it is removed on the board (an unsaved
  change that Save as Draft carries), and removing a subject's last slice publishes the
  removal through /api/schedule/approve.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / 'js' / 'manual_editor_state_harness.js'
NODE = shutil.which('node')


def _run(name):
    if not NODE:
        pytest.skip('node is not installed')
    out = subprocess.run([NODE, str(HARNESS), name], capture_output=True, text=True,
                         encoding='utf-8', timeout=240)
    data = json.loads(out.stdout)
    assert 'harness_error' not in data, data.get('harness_error')
    return data[name]


@pytest.fixture(scope='module')
def dirty():
    return _run('dirty_state')


@pytest.fixture(scope='module')
def removal():
    return _run('published_removal')


def test_editing_an_existing_slice_makes_the_editor_dirty(dirty):
    r = dirty['save_ok']
    assert r['clean']['unsaved'] is False
    assert r['edited']['unsaved'] is True


def test_successful_save_establishes_a_clean_baseline(dirty):
    assert dirty['save_ok']['after']['unsaved'] is False


@pytest.mark.parametrize('case', ['save_error', 'save_network'])
def test_failed_save_keeps_the_editor_dirty(dirty, case):
    assert dirty[case]['after']['unsaved'] is True


def test_successful_publish_establishes_a_clean_baseline(dirty):
    assert dirty['publish_ok']['edited']['unsaved'] is True
    assert dirty['publish_ok']['after']['unsaved'] is False


@pytest.mark.parametrize('case', ['publish_error', 'publish_network'])
def test_failed_publish_keeps_the_editor_dirty(dirty, case):
    assert dirty[case]['after']['unsaved'] is True


def test_saved_faculty_pick_is_no_longer_pending(dirty):
    assert dirty['faculty_saved'] == {'unsaved': False, 'removed': [], 'faculty': False}


def test_removing_a_published_slice_is_a_board_edit_not_a_delete(removal):
    b = removal['board']
    assert b['delete_called'] is False
    assert b['unsaved'] is True and b['removed'] == ['A101']
    assert b['hidden_mon'] is True and b['hidden_mon_after_rebuild'] is True


def test_save_draft_carries_the_remaining_published_slices(removal):
    b = removal['board']
    assert b['saved'] == ['Thursday|09:00 AM']
    assert b['after_save'] == {'unsaved': False, 'removed': []}


def test_discarding_a_board_removal_restores_the_published_slice(removal):
    assert removal['discard'] == {'removed': [], 'hidden_mon': False}


def test_removing_the_last_slice_changes_nothing_on_the_server(removal):
    r = removal['last_slice']['on_remove']
    # Only the calendar's read-only re-render; no delete, save or publish.
    assert all(u.startswith('/api/get_room_schedule/') for u in r['server_calls'])
    assert r['unsaved'] is True and r['removed'] == ['B201']


def test_save_draft_records_an_explicit_subject_removal_without_publishing(removal):
    r = removal['last_slice']['on_save']
    assert r['approve_called'] is False
    assert r['schedule_data'] == [] and r['removed_subjects'] == ['B201']
    assert r['unsaved_after'] is False


def test_publish_carries_the_saved_removal_explicitly(removal):
    assert removal['publish_removal'] == {'schedule_data': [], 'removed': ['B201'], 'section': '9'}


# ── Phase 11: every edit kind follows the same dirty/clean lifecycle ───────────

EDIT_KINDS = {
    'change_day':             ['Thursday', 'Wednesday'],
    'change_time':            ['Monday', 'Thursday'],
    'change_room':            ['Monday', 'Thursday'],
    'add_slice':              ['Friday', 'Monday', 'Thursday'],
    'change_faculty':         ['Monday', 'Thursday'],
    'multi_session_edit_one': ['Friday', 'Monday'],          # whole subject saved, not just the edit
}


@pytest.fixture(scope='module')
def kinds():
    return _run('edit_kinds')


@pytest.mark.parametrize('kind', sorted(EDIT_KINDS))
def test_edit_makes_dirty_and_successful_save_clears_it(kinds, kind):
    r = kinds[kind]['ok']
    assert r['edited'] is True and r['after'] is False
    assert r['saved_slots'] == EDIT_KINDS[kind]


@pytest.mark.parametrize('kind', sorted(EDIT_KINDS))
def test_failed_save_keeps_every_kind_of_edit_dirty(kinds, kind):
    r = kinds[kind]['error']
    assert r['edited'] is True and r['after'] is True


def test_confirmed_save_or_publish_drops_stale_room_occupancy_for_the_term():
    r = _run('cache_invalidation')
    for case in ('save_ok', 'publish_ok'):
        assert r[case] == {'this_term_stale': False, 'other_term_kept': True}, case
    # Nothing was persisted: caches are left alone.
    assert r['save_error'] == {'this_term_stale': True, 'other_term_kept': True}
