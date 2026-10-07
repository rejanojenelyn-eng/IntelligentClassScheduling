"""
Manual Scheduler Phase 2: vanishing / wrong-state fixes (RC1-RC5 + faculty fallback).

The frontend scenarios run the REAL manualEditor.acad2.js + manualScheduleEditor.html
inline scripts under Node (tests/js/manual_editor_state_harness.js). Subject A has two
Published slices (Mon s1001, Thu s1002) sharing one schedule_version (v100).

  RC1  failed Approve is client-transactional (restored on any non-success outcome)
  RC2  occurrence identity survives confirmAndPlace / Save Slice
  RC3  hiding is per occurrence (s:<sessionid>), never a sibling slice
  RC4  a late existing_sessions response never mutates state
  RC5  get_room_schedule: a Draft in another room suppresses the Published row
  +    an empty instructor name never auto-selects the first faculty
"""
import json
import shutil
import subprocess
from datetime import time
from pathlib import Path

import pytest

import app as app_module

HARNESS = Path(__file__).resolve().parent / 'js' / 'manual_editor_state_harness.js'
NODE = shutil.which('node')


@pytest.fixture(scope='module')
def h():
    if not NODE:
        pytest.skip('node is not installed')
    out = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True,
                         encoding='utf-8', timeout=240)
    data = json.loads(out.stdout)
    assert 'harness_error' not in data, data.get('harness_error')
    return data


def _mirrors(st):
    return {(p['subject'], p['day']): p for p in st['pending']}


# ── RC1 failed Approve ──────────────────────────────────────────────────────

@pytest.mark.parametrize('outcome', ['violations', 'error', 'network', 'replace_declined'])
def test_failed_approve_restores_state_exactly(h, outcome):
    r = h['failed_approve'][outcome]
    assert r['approve_called'] is True and r['result'] is False and r['in_flight'] is False
    assert r['after']['pending'] == r['before']['pending']        # mirrors + identity untouched
    assert r['after']['hidden'] == r['before']['hidden']
    assert r['after']['pills'] == r['before']['pills']


def test_failed_approve_keeps_the_proposed_edit_and_published_sibling(h):
    after = h['failed_approve']['violations']['after']
    m = _mirrors(after)
    wed, thu = m[('A101', 'Wednesday')], m[('A101', 'Thursday')]
    assert wed['fromExisting'] and wed['sessionid'] == 1001 and wed['loaded_day'] == 'Monday'
    assert thu['fromExisting'] and thu['sessionid'] == 1002 and thu['status'] == 'Published'
    assert after['pills'] == ['A101/Thursday', 'A101/Wednesday', 'B201/Tuesday']


# ── RC2 Save Slice / confirmAndPlace identity ──────────────────────────────

def test_save_slice_preserves_occurrence_identity(h):
    r = h['save_slice_identity']
    loaded = _mirrors(r['loaded'])[('A101', 'Monday')]
    saved = _mirrors(r['saved'])[('A101', 'Wednesday')]
    assert saved['temp_id'] == loaded['temp_id']
    for k in ('fromExisting', 'versionid', 'sessionid', 'official_sessionid', 'section_id', 'loaded_day', 'status'):
        assert saved[k] == loaded[k], k
    assert r['existing_json_unchanged'] is True                    # full original snapshot kept


# ── RC3 occurrence-level hiding ────────────────────────────────────────────

def test_hiding_is_per_session_not_per_version(h):
    only_thu = h['multi_slice_hiding']['onlyThursdayMirror']
    assert only_thu['hidden'] == ['s:1002']
    assert 'A101/Monday' in only_thu['pills']                      # sibling slice stays visible


def test_save_slice_then_switch_subject_no_longer_loses_a_slice(h):
    after = h['multi_slice_hiding']['afterSwitch']
    assert {'A101/Monday', 'A101/Thursday', 'B201/Tuesday'} <= set(after['pills'])
    assert _mirrors(after)[('A101', 'Monday')]['sessionid'] == 1001   # discarded edit reverted


# ── RC4 late responses / section isolation ─────────────────────────────────

def test_late_response_for_a_never_alters_state(h):
    r = h['late_response']
    for k in ('afterStaleToken', 'afterNoToken'):
        assert r[k]['pending'] == r['loaded']['pending'], k
        assert r[k]['hidden'] == r['loaded']['hidden'], k
        assert r[k]['pills'] == r['loaded']['pills'], k


def test_existing_sessions_token_rules(h):
    assert h['token_rules'] == {'current': True, 'newer_request': False, 'other_subject': False,
                                'other_section': False, 'no_token': True}


def test_section_isolation(h):
    r = h['section_isolation']
    assert r['afterLate1A']['pending'] == r['loaded1A']['pending']   # late 1A response ignored on 1B
    assert r['afterLate1A']['hidden'] == r['loaded1A']['hidden']
    loaded1b = r['loaded1B']
    assert [p['section_id'] for p in loaded1b['pending']] == ['10']
    assert loaded1b['hidden'] == ['s:1101']
    assert set(loaded1b['pills']) == {'A101/Monday', 'A101/Thursday', 'A101/Friday'}


def test_empty_instructor_name_never_selects_the_first_faculty(h):
    r = h['empty_faculty_fallback']
    assert r['mirrors'] == 1 and r['sel_faculty'] == ''


# ── RC5 get_room_schedule: Draft replacement across rooms ──────────────────

def _row(code='A101', section=9, status='Published', day='Monday', st=4, sem=1, vid=100, sid=1001):
    return {'subjectcode': code, 'subjectname': code, 'employee_number': 'F1', 'instructor': 'X',
            'daydesc': day, 'starttimeid': st, 'endtimeid': st + 3, 'year_level': 1,
            'programcode': 'BSIT', 'sectionname': 'S', 'section_id': section, 'roomname': 'R5',
            'status': status, 'versionid': vid, 'official_sessionid': sid,
            'schedule_source': 'Official', 'semesterid': sem}


class _Cur:
    def __init__(self, room_rows, drafted):
        self.room_rows, self.drafted, self.calls, self._last = room_rows, drafted, [], None

    def execute(self, sql, params=None):
        self.calls.append(sql)
        self._last = [dict(r) for r in (self.drafted if "sv.status = 'Draft'" in sql and 'ANY(%s)' in sql
                                        else self.room_rows)]

    def fetchall(self):
        return self._last

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, **k):
        return self.cur

    def close(self):
        pass


def _room(monkeypatch, room_rows, drafted, mode='official'):
    cur = _Cur(room_rows, drafted)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _Conn(cur))
    monkeypatch.setattr(app_module, '_ensure_local_tables', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='mse-test')
    body = client.get(f'/api/get_room_schedule/5?ay_id=AY2627&semester=A&scheduler_mode={mode}').get_json()
    return body, cur


def test_draft_in_another_room_suppresses_the_published_row(monkeypatch):
    body, _ = _room(monkeypatch, [_row()], [{'subj': 'A101', 'sectionid': 9, 'semesterid': 1}])
    assert body == []


def test_published_row_kept_when_no_draft_exists(monkeypatch):
    body, _ = _room(monkeypatch, [_row()], [])
    assert [r['subjectcode'] for r in body] == ['A101']


def test_draft_of_another_section_does_not_suppress(monkeypatch):
    body, _ = _room(monkeypatch, [_row()], [{'subj': 'A101', 'sectionid': 10, 'semesterid': 1}])
    assert [r['section_id'] for r in body] == [9]


def test_in_room_draft_still_replaces_and_relabels_matching_slot(monkeypatch):
    rows = [_row(), _row(status='Draft', vid=300, sid=3001), _row(status='Published', day='Thursday', sid=1002)]
    body, _ = _room(monkeypatch, rows, [{'subj': 'A101', 'sectionid': 9, 'semesterid': 1}])
    assert [(r['daydesc'], r['status'], r['versionid']) for r in body] == [('Monday', 'Published', 300)]


def test_local_mode_does_not_run_the_draft_lookup(monkeypatch):
    local_row = dict(_row(), status='Local', versionid=None, schedule_source='Local')
    body, cur = _room(monkeypatch, [local_row], [{'subj': 'A101', 'sectionid': 9, 'semesterid': 1}], mode='local')
    assert len(body) == 1
    assert not any("sv.status = 'Draft'" in sql and 'ANY(%s)' in sql for sql in cur.calls)
