"""A plain Generate must come out clean: an unlucky randomized run that leaves a class
Incomplete is retried with new seeds (best result kept), and each advisory notice is
listed once."""
from datetime import time

import pytest

import app as app_module


def _gene(code, incomplete=False):
    g = {'subject_code': code, 'description': code, 'faculty_id': None if incomplete else '89127',
         'instructor': 'TBA' if incomplete else 'Dela Cruz, Juan', 'room_id': 1, 'room': 'LQ101',
         'day': 'Monday', 'days_list': ['Monday'], 'start_time': time(7, 30), 'end_time': time(9, 0),
         'lec_hours': 1.5, 'lab_hours': 0, 'units': 3, 'class_type': 'Lecture'}
    if incomplete:
        g.update(incomplete=True, incomplete_reason=['Instructor is not assigned.'])
    return g


_ADVISORY = {'rule': 'HC_SPEC', 'severity': 'warning', 'subject': 'AAA 101',
             'detail': '"X" may not match the expected specialization for "AAA 101".'}


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_generate(*a, seed=None, **k):
        calls.append(seed)
        bad = len(calls) == 1                    # first run unlucky, later runs clean
        return {'success': True, 'result_status': 'PARTIAL_VALID' if bad else 'COMPLETE_VALID',
                'schedule_data': [_gene('AAA 101'), _gene('BBB 102', incomplete=bad)],
                'violations': [dict(_ADVISORY), dict(_ADVISORY)],   # same notice per session
                'conflict_count': 0, 'incomplete_count': 1 if bad else 0,
                'completion_rate': 50.0 if bad else 100.0}

    monkeypatch.setattr(app_module.scheduler_engine, 'generate_draft', fake_generate)
    monkeypatch.setattr(app_module, '_check_cross_schedule_conflicts', lambda *a, **k: ([], 0))
    monkeypatch.setattr(app_module, 'write_activity_log', lambda *a, **k: None)
    c = app_module.app.test_client()
    u = app_module.query_db("SELECT username FROM accounts WHERE isactive LIMIT 1", one=True)
    if not u:
        pytest.skip('no active account')
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username=u['username'],
                 account_setup_complete=True, must_change_password=False)
    return c, calls


def test_unlucky_run_is_retried_and_best_kept(client):
    c, calls = client
    d = c.post('/api/schedule/generate', json={'program': 'AAA', 'yearLevel': 1, 'term': 'A',
                                               'acadYear': 'AY2627', 'curriculum': '2022-2023'}).get_json()
    assert len(calls) == 2 and calls[1] is not None        # retried once, with a new seed
    assert d['result_status'] == 'COMPLETE_VALID'
    # the kept result is the clean run's: BBB 102 has its instructor
    # (rows may still be flagged by the real-curriculum reference check -- fake codes)
    bbb = next(r for r in d['schedule_data'] if r['subject_code'] == 'BBB 102')
    assert bbb.get('faculty_id') == '89127'
    assert sum(1 for v in d['violations'] if v.get('rule') == 'HC_SPEC') == 1   # de-duplicated


def test_retry_is_capped_and_regenerate_selected_is_not_retried():
    src = open(app_module.__file__, encoding='utf-8').read()
    assert '_FRESH_GEN_ATTEMPTS = 8' in src
    block = src[src.index('# ── Fresh generation must come out clean'):]
    assert block.index('if not locked_sessions:') < 1000
