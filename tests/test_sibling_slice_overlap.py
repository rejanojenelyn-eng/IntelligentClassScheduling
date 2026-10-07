"""
Sibling-slice overlap: two occurrences of the SAME subject for the SAME section that
overlap in time (e.g. EDUC 021 Saturday 3:00-6:00 PM and 5:30-8:30 PM).

Frontend (real acad2.js + template inline scripts via tests/js/manual_editor_state_harness.js,
scenario `sibling_overlap`): confirmAndPlace reports it as a sibling overlap BEFORE the
room/faculty checks; a failed Save restores the exact pre-save state (preview kept) and
redraws; a failed Approve restores the exact state.

Server: api_save_draft blocks it (400, nothing written) and api_validate_schedule reports
it, so a client that bypasses the editor's checks cannot persist it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import app as app_module

HARNESS = Path(__file__).resolve().parent / 'js' / 'manual_editor_state_harness.js'
NODE = shutil.which('node')
SIBLING_MSG = 'A101 Slice overlaps another slice of the same subject'


@pytest.fixture(scope='module')
def sib():
    if not NODE:
        pytest.skip('node is not installed')
    out = subprocess.run([NODE, str(HARNESS), 'sibling_overlap'], capture_output=True, text=True,
                         encoding='utf-8', timeout=240)
    data = json.loads(out.stdout)
    assert 'harness_error' not in data, data.get('harness_error')
    return data['sibling_overlap']


def _unchanged(r):
    for k in ('pending', 'hidden', 'pills', 'dirty'):
        assert r['after'][k] == r['before'][k], k


# ── frontend: Save ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('case,expected', [
    ('new_same_room',  '(Monday 9:00 AM–10:30 AM).'),
    ('new_other_room', '(Monday 9:00 AM–10:30 AM).'),
    ('edited',         '(Thursday 9:00 AM–10:30 AM).'),
    ('db_only',        '(Thursday 9:00 AM–10:30 AM).'),   # sibling known only from the DB
])
def test_save_blocks_sibling_overlap_with_a_clear_message(sib, case, expected):
    r = sib[case]
    assert r['modals'] == [f'CONFLICT {SIBLING_MSG} {expected}']
    assert not any('Room conflict' in m or 'Faculty conflict' in m for m in r['modals'])
    assert r['calls'] == []                                 # validate/save never reached


@pytest.mark.parametrize('case', ['new_same_room', 'new_other_room', 'edited', 'db_only'])
def test_failed_save_restores_exact_state_and_redraws(sib, case):
    r = sib[case]
    _unchanged(r)
    assert r['redrawn'] is True


def test_failed_save_keeps_the_new_slice_preview_visible(sib):
    r = sib['new_same_room']
    assert any(p['temp_id'].startswith('PREVIEW_') for p in r['after']['pending'])
    assert r['after']['pills'].count('A101/Monday') == 2    # Published slice + attempted slice


def test_failed_save_keeps_the_attempted_edit_and_identity(sib):
    after = sib['edited']['after']
    edited = [p for p in after['pending'] if p['start'] == '09:30 AM']
    assert len(edited) == 1 and edited[0]['day'] == 'Thursday'
    assert edited[0]['fromExisting'] and edited[0]['sessionid'] == 1001


def test_server_rejected_save_restores_exact_state_and_redraws(sib):
    r = sib['server_rejects']
    assert r['modals'] == ['VIOLATION server says no']
    assert '/api/schedule/save-draft' in r['calls']
    _unchanged(r)
    assert r['redrawn'] is True


def test_non_overlapping_slice_still_saves(sib):
    r = sib['adjacent']
    assert r['modals'] == ['Saved as Draft']
    assert '/api/schedule/save-draft' in r['calls']


def test_draft_supersedes_published_rows_in_the_sibling_check(sib):
    # The subject's Draft (Friday) replaces its Published Thursday row, so that stale
    # row is not a sibling.
    assert not any(SIBLING_MSG in m for m in sib['db_draft_supersedes']['modals'])


# ── frontend: Approve ───────────────────────────────────────────────────────

@pytest.mark.parametrize('case', ['approve_new', 'approve_edited'])
def test_approve_blocks_sibling_overlap_and_restores_exact_state(sib, case):
    r = sib[case]
    assert len(r['modals']) == 1 and r['modals'][0].startswith(f'CONFLICT {SIBLING_MSG}')
    assert '/api/schedule/approve' not in r['calls']
    _unchanged(r)


# ── server ──────────────────────────────────────────────────────────────────

def _row(start, end, day='Saturday', code='EDUC 021', fac='20123', **extra):
    return dict({'subject_code': code, 'day': day, 'days_list': [day], 'start_time': start,
                 'end_time': end, 'faculty_id': fac, 'room_id': '5', 'room_type': 'Lecture'}, **extra)


def test_helper_flags_same_subject_same_section_overlap():
    v = app_module._same_subject_slice_overlaps(
        [_row('03:00 PM', '06:00 PM'), _row('05:30 PM', '08:30 PM')], same_section=True)
    assert [x['rule'] for x in v] == ['HC12', 'HC10']
    assert v[0]['detail'] == ('EDUC 021 Slice overlaps another slice of the same subject '
                              '(Saturday 3:00 PM–6:00 PM and 5:30 PM–8:30 PM).')


def test_helper_no_hc10_without_a_shared_assigned_faculty():
    v = app_module._same_subject_slice_overlaps(
        [_row('03:00 PM', '06:00 PM', fac='TBA'), _row('05:30 PM', '08:30 PM', fac='TBA')], same_section=True)
    assert [x['rule'] for x in v] == ['HC12']


@pytest.mark.parametrize('rows,same_section', [
    ([_row('03:00 PM', '06:00 PM'), _row('06:00 PM', '08:30 PM')], True),                 # adjacent
    ([_row('03:00 PM', '06:00 PM'), _row('05:30 PM', '08:30 PM', day='Friday')], True),    # other day
    ([_row('03:00 PM', '06:00 PM'), _row('05:30 PM', '08:30 PM', code='EDUC 022')], True),  # other subject
    ([_row('03:00 PM', '06:00 PM', section_id='467'),
      _row('05:30 PM', '08:30 PM', section_id='468')], False),                              # other section
    ([_row('03:00 PM', '06:00 PM'), _row('05:30 PM', '08:30 PM')], False),                  # identity unknown
])
def test_helper_ignores_non_siblings(rows, same_section):
    assert app_module._same_subject_slice_overlaps(rows, same_section=same_section) == []


def test_helper_matches_explicit_section_identity_without_context():
    v = app_module._same_subject_slice_overlaps(
        [_row('03:00 PM', '06:00 PM', section_id='467'), _row('05:30 PM', '08:30 PM', section_id=467)])
    assert v and v[0]['rule'] == 'HC12'


class _Cur:
    def __init__(self, log):
        self.log, self._one = log, None

    def execute(self, sql, params=None):
        self.log.append(' '.join(sql.split()))
        self._one = {'semesterid': 1} if 'FROM public.semester' in sql else {'max_v': 0}

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def close(self):
        pass


class _Conn:
    def __init__(self, log):
        self.log, self.committed = log, False

    def cursor(self, **k):
        return _Cur(self.log)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _client(monkeypatch):
    # before_request account-status guard (same stub as test_manual_editor_state._room)
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='sib-test')
    return client


def test_save_draft_bypass_cannot_save_a_sibling_overlap(monkeypatch):
    log, conns = [], []

    def _conn():
        conns.append(_Conn(log))
        return conns[-1]

    monkeypatch.setattr(app_module, 'get_db_connection', _conn)
    monkeypatch.setattr(app_module, '_source_col_ensured', True, raising=False)
    monkeypatch.setattr(app_module, '_original_status_col_ensured', True, raising=False)
    monkeypatch.setattr(app_module, '_schedule_version_empnum_col_ensured', True, raising=False)
    body = {'schedule_data': [_row('03:00 PM', '06:00 PM'), _row('05:30 PM', '08:30 PM')],
            'context': {'program': 'BEED', 'yearLevel': 4, 'term': 'A', 'acadYear': 'AY2627',
                        'section_id': '467'}}
    res = _client(monkeypatch).post('/api/schedule/save-draft', json=body)
    data = res.get_json()
    assert res.status_code == 400 and data['success'] is False
    assert [v['rule'] for v in data['violations']] == ['HC12', 'HC10']
    assert not any(c.committed for c in conns)
    assert not any(s.startswith(('INSERT', 'UPDATE', 'DELETE')) for s in log)


def test_validate_reports_a_sibling_overlap(monkeypatch):
    class _Svc:
        def validate_schedule(self, *a, **k):
            return type('R', (), {'violations': []})()

    monkeypatch.setattr(app_module, '_load_faculty_map', lambda: {})
    monkeypatch.setattr(app_module.ConstraintService, 'with_current_policy', classmethod(lambda cls: _Svc()))
    rows = [_row('03:00 PM', '06:00 PM', section_id='467'), _row('05:30 PM', '08:30 PM', section_id='467')]
    data = _client(monkeypatch).post('/api/schedule/validate', json={'schedule_data': rows}).get_json()
    assert data['has_violations'] is True
    assert data['violations'][0]['rule'] == 'HC12'
    assert 'overlaps another slice of the same subject' in data['violations'][0]['detail']
