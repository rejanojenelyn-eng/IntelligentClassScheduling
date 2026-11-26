"""Manual Editor, Settings > Shared Subjects: a faculty per slice.

Runs the REAL editor scripts under Node (tests/js/manual_editor_state_harness.js,
scenario `shared_subject`): subject A101 shared by up to 2 faculty, Monday's slice taught
by the subject's faculty F1 (Dela Cruz) and Thursday's by F2 (Santos).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / 'js' / 'manual_editor_state_harness.js'
NODE = shutil.which('node')


@pytest.fixture(scope='module')
def r():
    if not NODE:
        pytest.skip('node is not installed')
    out = subprocess.run([NODE, str(HARNESS), 'shared_subject'], capture_output=True, text=True,
                         encoding='utf-8', timeout=240)
    data = json.loads(out.stdout)
    assert 'harness_error' not in data, data.get('harness_error')
    assert data['shared_subject']['load_errors'] == []
    return data['shared_subject']


def test_loaded_slices_keep_their_own_faculty(r):
    assert r['loaded']['locked'] == 'F1'                       # the subject's faculty, not slice 1's
    assert r['loaded']['faculty'] == {'Monday': 'F1', 'Thursday': 'F2'}
    assert r['loaded']['card_value'] == ['', 'F2']             # '' = the subject's faculty


def test_assigned_faculty_panel_and_slice_choices(r):
    assert r['loaded']['assigned'] == ['F1', 'F2']            # subject's faculty first
    assert r['loaded']['slice_options'] == ['', 'F2']          # a slice picks only from them


def test_panel_refuses_a_faculty_over_the_limit_or_one_still_teaching(r):
    assert r['addOverLimit'] == {'assigned': ['F1', 'F2'], 'modals': ['Too Many Faculty']}
    assert r['removeBusy'] == {'assigned': ['F1', 'F2'], 'modals': ['Faculty Still Has Slices']}


def test_calendar_pill_suffix_names_the_faculty(r):
    assert r['loaded']['suffix'] == [' · DELA CRUZ', ' · SANTOS']


def test_more_faculty_than_the_limit_is_refused_before_saving(r):
    assert r['loaded']['limit_ok'] is True
    assert r['overLimit'] == {'ok': False, 'modals': ['Too Many Faculty']}


def test_changing_a_slices_faculty_is_saved_as_a_change(r):
    assert r['afterMove'] == {'Monday': 'F1', 'Thursday': 'F1'}
    assert r['afterBack']['faculty'] == {'Monday': 'F1', 'Thursday': 'F2'}
    assert r['afterBack']['no_change_modal'] is False


def test_ordinary_subject_is_unchanged(r):
    o = r['ordinary']
    assert o['faculty'] == {'Monday': 'F1', 'Thursday': 'F1'}  # every slice: the subject's faculty
    assert o['locked'] == 'F1' and o['suffix'] == '' and o['card_value'] == ''
