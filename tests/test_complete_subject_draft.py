"""
Complete subject Draft: a Draft is the COMPLETE schedule of each subject+section it
touches, because the server treats it as one replacement of that subject's Published
schedule (existing_sessions returns the Draft alone; Approve replaces the subject).

Frontend (real acad2.js + template inline scripts, tests/js/manual_editor_state_harness.js):
  complete_draft        Save -> Draft rows (as api_save_draft/existing_sessions produce them)
                        -> reload -> Approve, for edit / add / edit+add / remove+edit / re-save
  complete_draft_rules  _completeSubjectDrafts dedup + scoping
  sibling_overlap       db_draft_supersedes: stale Published rows of a Drafted subject
                        raise no Section conflict
Server: /api/manual/section_schedule (official) drops Published rows whose
subject+section+semester has a Draft.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import app as app_module

HARNESS = Path(__file__).resolve().parent / 'js' / 'manual_editor_state_harness.js'
NODE = shutil.which('node')
MON, THU, WED, FRI = ('Monday|09:00 AM-10:30 AM', 'Thursday|09:00 AM-10:30 AM',
                      'Wednesday|09:00 AM-10:30 AM', 'Friday|09:00 AM-10:30 AM')


def _run(*scenarios):
    if not NODE:
        pytest.skip('node is not installed')
    out = subprocess.run([NODE, str(HARNESS), *scenarios[:1]], capture_output=True, text=True,
                         encoding='utf-8', timeout=240)
    data = json.loads(out.stdout)
    assert 'harness_error' not in data, data.get('harness_error')
    return data[scenarios[0]]


@pytest.fixture(scope='module')
def cd():
    return _run('complete_draft')


def _slots(r):
    return sorted(x['slot'] for x in r['saved'])


# ── Save: the Draft payload is the complete subject ────────────────────────

def test_edit_one_of_two_published_slices_saves_both(cd):
    r = cd['edit_one']
    assert r['save_modals'] == ['Saved as Draft']
    assert _slots(r) == sorted([THU, WED])
    by = {x['slot']: x for x in r['saved']}
    assert by[WED]['sessionid'] == 1001 and by[WED]['fromExisting'] is False   # edited occurrence
    assert by[THU]['sessionid'] == 1002 and by[THU]['fromExisting'] is True    # unchanged sibling
    assert {x['section_id'] for x in r['saved']} == {'9'}


@pytest.mark.parametrize('case,expected', [
    ('edit_one',     [THU, WED]),
    ('add_one',      [FRI, MON, THU]),
    ('edit_add',     [FRI, THU, WED]),
    ('resave_draft', [FRI, THU]),       # over an existing Draft (Thu reads back as 'Published')
    ('remove_edit',  [WED]),            # removed Thu was deleted server-side; never resurrected
])
def test_complete_draft_for_edit_add_remove(cd, case, expected):
    assert _slots(cd[case]) == sorted(expected)


@pytest.mark.parametrize('case', ['edit_one', 'add_one', 'edit_add', 'resave_draft', 'remove_edit'])
def test_no_duplicate_siblings(cd, case):
    saved = cd[case]['saved']
    assert len({x['slot'] for x in saved}) == len(saved)
    ids = [x['sessionid'] for x in saved if x['sessionid'] is not None]
    assert len(set(ids)) == len(ids)


# ── Reload + Approve ───────────────────────────────────────────────────────

@pytest.mark.parametrize('case,expected', [
    ('edit_one',     ['Thursday|09:00 AM', 'Wednesday|09:00 AM']),
    ('add_one',      ['Friday|09:00 AM', 'Monday|09:00 AM', 'Thursday|09:00 AM']),
    ('edit_add',     ['Friday|09:00 AM', 'Thursday|09:00 AM', 'Wednesday|09:00 AM']),
    ('resave_draft', ['Friday|09:00 AM', 'Thursday|09:00 AM']),
    ('remove_edit',  ['Wednesday|09:00 AM']),
])
def test_reloaded_draft_shows_every_slice(cd, case, expected):
    assert cd[case]['reloaded'] == expected


@pytest.mark.parametrize('case', ['edit_one', 'add_one', 'edit_add', 'resave_draft', 'remove_edit'])
def test_approve_publishes_the_complete_draft(cd, case):
    r = cd[case]
    assert r['approve_modals'] == ['Schedule Published']
    assert r['approved'] == _slots(r)


def test_completion_rules_scope_and_dedup():
    # D1 = edited Mon (sessionid 1001). Kept: unchanged Thu once. Dropped: the stale
    # Mon mirror (same occurrence), the duplicate Thu mirror, another section's mirror,
    # a preview, and a mirror of an untouched subject.
    assert _run('complete_draft_rules') == ['D1', 'M-thu']


# ── Section conflict: a Drafted subject's old Published rows ───────────────

def test_superseded_published_rows_raise_no_section_conflict_in_the_editor():
    r = _run('sibling_overlap')['db_draft_supersedes']
    assert r['modals'] == ['Saved as Draft']
    assert not any('Section conflict' in m for m in r['modals'])


class _Capture:
    def __init__(self):
        self.sql = []

    def __call__(self, sql, params=None, *a, **k):
        self.sql.append(' '.join(sql.split()))
        return []


def _section_schedule_sql(monkeypatch, mode):
    from flask import session
    cap = _Capture()
    monkeypatch.setattr(app_module, 'query_db', cap)
    q = (f'/api/manual/section_schedule?program=BSIT&year_level=1&ay_id=AY2627'
         f'&semester=A&section_id=9&scheduler_mode={mode}')
    with app_module.app.test_request_context(q):
        session['loggedin'] = True
        app_module.api_manual_section_schedule()
    return cap.sql


def test_section_schedule_drops_published_rows_superseded_by_a_draft(monkeypatch):
    sql = _section_schedule_sql(monkeypatch, 'official')
    assert len(sql) == 1
    s = sql[0]
    assert "NOT ( sv.status = 'Published' AND EXISTS" in s
    assert "sv_d.status = 'Draft'" in s
    assert 'sc_d.sectionid = sc.sectionid' in s
    assert 'sc_d.semesterid = sc.semesterid' in s
    assert 'UPPER(cs_d.subjectcode) = UPPER(cs.subjectcode)' in s


def test_local_section_schedule_is_unchanged(monkeypatch):
    assert not any('sv_d.status' in s for s in _section_schedule_sql(monkeypatch, 'local'))
