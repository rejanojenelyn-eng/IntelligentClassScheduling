"""
Constraint-fix Phase 3: HC10 / HC16 consistency.

One shared faculty-overlap exemption (faculty_load.faculty_overlap_exempt):
valid HC16 merge (merge scope + section-pair rules) OR the long-standing
NSTP/OU shared-faculty exemption. Missing/TBA faculty never triggers HC10.
HC12 is never exempted by HC16.

Before this phase the three validators disagreed:
  * CSPValidator used ONLY the NSTP exemption -> flagged a valid same-faculty
    merge (e.g. all_subjects scope) as HC10;
  * cross-schedule (HC15) and Local used ONLY HC16 -> flagged two different
    NSTP/OU subjects taught by one faculty, which CSP allowed;
  * CSP flagged two unassigned rows because None == None.
The same scenario now produces the same HC10 answer in all three.
No real database is touched.
"""
from datetime import time
from pathlib import Path

import pytest

import app as app_module
import database
import faculty_load
from constraints.context_conflicts import overlapping_resource_conflicts
from scheduler import CSPValidator

APP_SRC = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')

NSTP_ONLY = {'hc_merge_enabled': 1, 'hc_merge_scope': 'nstp_only'}
ALL_SUBJECTS = {'hc_merge_enabled': 1, 'hc_merge_scope': 'all_subjects'}
ALL_SUBJECTS_PAIR_AC = dict(ALL_SUBJECTS, hc_merge_section_pairs='[["BSIT-A","BSIT-C"]]')
MERGE_OFF = {'hc_merge_enabled': 0}

# (id, a_subject, b_subject, faculty, config, expect_hc10)
SCENARIOS = [
    ('different_subjects_same_faculty', 'IT101', 'IT102', 'F1', ALL_SUBJECTS, True),
    ('two_nstp_subjects_same_faculty', 'NSTP101', 'NSTP102', 'F1', NSTP_ONLY, False),
    ('nstp_exemption_survives_merge_off', 'NSTP101', 'OU201', 'F1', MERGE_OFF, False),
    ('valid_same_faculty_merge_in_scope', 'IT101', 'IT101', 'F1', ALL_SUBJECTS, False),
    ('same_subject_out_of_merge_scope', 'IT101', 'IT101', 'F1', NSTP_ONLY, True),
    ('merge_section_pair_not_allowed', 'IT101', 'IT101', 'F1', ALL_SUBJECTS_PAIR_AC, True),
    ('tba_faculty_never_conflicts', 'IT101', 'IT102', 'TBA', ALL_SUBJECTS, False),
    ('missing_faculty_never_conflicts', 'IT101', 'IT102', None, ALL_SUBJECTS, False),
]
IDS = [s[0] for s in SCENARIOS]


# ── 1. CSPValidator ─────────────────────────────────────────────────────────

def _csp_hc10(a_code, b_code, fac, cfg):
    a = {'subject_code': a_code, 'faculty_id': fac, 'room_id': 1, 'course': 'BSIT',
         'section_name': 'A', 'day': 'Monday', 'days_list': ['Monday'],
         'start_time': time(9, 0), 'end_time': time(10, 30)}
    b = dict(a, subject_code=b_code, room_id=2, section_name='B')
    rules = {v['rule'] for v in CSPValidator(config=cfg)._check_faculty_overlaps([a, b])}
    return 'HC10' in rules


# ── 2. cross-schedule (HC15) check ──────────────────────────────────────────

class _CrossCursor:
    def __init__(self, published):
        self.published, self._pending = published, None

    def execute(self, query, params=None):
        if 'FROM semester' in query:
            self._pending = {'semesterid': 1}
        elif 'FROM   schedule_sessions ss' in query:
            self._pending = self.published
        else:
            self._pending = []

    def fetchone(self):
        return self._pending

    def fetchall(self):
        return self._pending or []

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, **k):
        return self.cur

    def close(self):
        pass


def _cross_hc10(monkeypatch, a_code, b_code, fac, cfg):
    published = [{'roomid': 2, 'faculty_id': fac, 'sectionid': 2, 'day': 'Monday',
                  'start_time': time(9, 0), 'end_time': time(10, 30), 'subjectcode': b_code,
                  'programcode': 'BSIT', 'yearlevel': 1, 'sectionname': 'B', 'roomname': 'R2'}]
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _Conn(_CrossCursor(published)))
    monkeypatch.setattr(database, 'load_scheduler_config', lambda: cfg)
    new = {'subject_code': a_code, 'faculty_id': fac, 'room_id': 1, 'section_name': 'A',
           'days_list': ['Monday'], 'start_time': time(9, 0), 'end_time': time(10, 30)}
    viols, _ = app_module._check_cross_schedule_conflicts([new], 'BSIT', 1, 'A', 'AY2627')
    return 'HC10' in {v['rule'] for v in viols}


# ── 3. Local Scheduler conflict endpoint ────────────────────────────────────

class _LocalCursor:
    def __init__(self, official_rows):
        self.official_rows, self.last = official_rows, ''

    def execute(self, sql, params=None):
        self.last = sql

    def fetchone(self):
        if 'SELECT sectionname FROM public.sections' in self.last:
            return {'sectionname': 'A'}
        if 'ORDER BY sv.version_number DESC' in self.last:
            return {'versionid': 1}
        return None

    def fetchall(self):
        return [dict(r) for r in self.official_rows] if "'Official' AS source" in self.last else []

    def close(self):
        pass


def _local_hc10(monkeypatch, a_code, b_code, fac, cfg):
    official = [{'source': 'Official', 'official_sessionid': 900, 'sectionid': 2,
                 'sectionname': 'B', 'programcode': 'BSIT', 'yearlevel': 1,
                 'subjectcode': b_code, 'daydesc': 'Monday', 'roomid': 2,
                 'employeenumber': fac, 'start_fmt': '09:00 AM', 'end_fmt': '10:30 AM',
                 'instructor': 'x'}]
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _Conn(_LocalCursor(official)))
    monkeypatch.setattr(database, 'load_scheduler_config', lambda: cfg)
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    for name, value in {
        '_ensure_local_tables': lambda *a, **k: None,
        '_get_semester_id': lambda *a, **k: 1,
        '_validate_local_scope_binding': lambda *a, **k: True,
        '_validate_local_official_session_binding': lambda *a, **k: True,
        '_get_official_occurrence_faculty': lambda *a, **k: (fac, True),
        '_validate_local_session_structure': lambda *a, **k: (True, None),
        '_validate_local_editable_dimensions': lambda *a, **k: (True, None),
    }.items():
        monkeypatch.setattr(app_module, name, value)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='phase3-test')
    body = client.post('/api/local/check_room_conflicts', json={
        'ay_id': 'AY2627', 'term': 'A', 'program': 'BSIT', 'year_level': 1, 'section_id': 1,
        'sessions': [{'subject_code': a_code, 'day': 'Monday', 'starttimeid': 4,
                      'endtimeid': 7, 'room_id': 1, 'official_sessionid': 100}],
    }).get_json()
    assert body['success'] is True, body
    return any(c['conflict_type'] == 'Faculty' for c in body['conflicts'])


# ── the same scenario, three validators, one answer ─────────────────────────

@pytest.mark.parametrize('sid,a_code,b_code,fac,cfg,expect', SCENARIOS, ids=IDS)
def test_hc10_is_consistent_across_csp_cross_schedule_and_local(
        monkeypatch, sid, a_code, b_code, fac, cfg, expect):
    results = {
        'csp': _csp_hc10(a_code, b_code, fac, cfg),
        'cross_schedule': _cross_hc10(monkeypatch, a_code, b_code, fac, cfg),
        'local': _local_hc10(monkeypatch, a_code, b_code, fac, cfg),
    }
    assert results == {k: expect for k in results}, (sid, results)


# ── shared rule + HC12 is never exempted ────────────────────────────────────

def test_shared_rule_definition():
    nstp = {'subject_code': 'NSTP101', 'faculty_id': 'F1'}
    ou = {'subject_code': 'OU201', 'faculty_id': 'F1'}
    it = {'subject_code': 'IT101', 'faculty_id': 'F1'}
    assert faculty_load.faculty_overlap_exempt(nstp, ou, config=MERGE_OFF) is True
    assert faculty_load.faculty_overlap_exempt(it, dict(it), config=ALL_SUBJECTS) is True
    assert faculty_load.faculty_overlap_exempt(it, dict(it), config=NSTP_ONLY) is False
    assert faculty_load.faculty_overlap_exempt(it, dict(it), valid_merge=True) is True
    for missing in (None, '', 'TBA', ' tba '):
        assert faculty_load.has_assigned_faculty(missing) is False
    assert faculty_load.has_assigned_faculty('F1') is True


def test_csp_nstp_helper_delegates_to_the_shared_definition():
    csp = CSPValidator(config=MERGE_OFF)
    a, b = {'subject_code': 'NSTP101'}, {'subject_code': 'OU201'}
    assert csp._nstp_shared_faculty_exempt(a, b) is faculty_load.nstp_shared_faculty_exempt(a, b) is True


def test_hc16_exemption_never_suppresses_hc12_in_csp():
    a = {'subject_code': 'IT101', 'faculty_id': 'F1', 'room_id': 1, 'course': 'BSIT',
         'section_name': 'A', 'day': 'Monday', 'days_list': ['Monday'],
         'start_time': time(9, 0), 'end_time': time(10, 30)}
    b = dict(a, room_id=2)                               # same section, valid merge shape
    rules = {v['rule'] for v in CSPValidator(config=ALL_SUBJECTS).validate([a, b], {})}
    assert 'HC10' not in rules and 'HC12' in rules


def test_resource_helper_faculty_exemption_is_separate_from_room_and_section():
    a = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 1}
    b = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 1}
    # NSTP-style case: faculty exempt, but not a valid merge -> room + section remain.
    assert overlapping_resource_conflicts(a, b, valid_merge=False, faculty_exempt=True) == ['Room', 'Section']
    tba = {'faculty_id': 'TBA', 'room_id': None, 'section_id': None}
    assert overlapping_resource_conflicts(tba, dict(tba)) == []


def test_all_local_faculty_sites_use_the_shared_rule():
    local = APP_SRC[APP_SRC.index('def api_local_check_room_conflicts'):
                    APP_SRC.index("@app.route('/api/get_room_schedule")]
    # 1 definition + 4 call sites: candidate-vs-candidate, Published Official,
    # Published Local, and pending Official Drafts of the same faculty.
    assert local.count('_faculty_overlap_exempt(') == 5
    assert "not merged and c['employeenumber']" not in local
