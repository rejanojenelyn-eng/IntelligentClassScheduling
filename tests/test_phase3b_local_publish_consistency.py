"""
Constraint-fix Phase 3 (completion): Local Publish uses the same HC10/HC11/HC12
rules as Local conflict checking.

Before: api_publish_local_arrangement's revalidation had NO HC16 at all -- any
same-faculty overlap was 'Faculty' and any same-room overlap was 'Room'. So a
Draft that /api/local/check_room_conflicts accepted (valid merge, NSTP/OU shared
faculty) could still be rejected at Publish. Now both endpoints decide through
faculty_load.faculty_overlap_exempt (HC10), a valid HC16 merge (HC11), and an
unconditional same-section check (HC12).
Both real endpoints are driven with a fake cursor; no real database is touched.
"""
from datetime import datetime

import pytest

import app as app_module
import database
from test_phase3_hc10_hc16_consistency import SCENARIOS, IDS, ALL_SUBJECTS, NSTP_ONLY, _Conn, _local_hc10


class _PublishCursor:
    def __init__(self, arr, draft, official, local):
        self.arr, self.draft, self.official, self.local = arr, draft, official, local
        self.last, self.rowcount = '', 0

    def execute(self, sql, params=None):
        self.last = sql
        self.rowcount = 1 if 'RETURNING updated_at' in sql else 0

    def fetchone(self):
        s = self.last
        if 'SELECT * FROM public.local_arrangement' in s:
            return dict(self.arr)
        if 'ORDER BY sv.version_number DESC' in s:
            return {'versionid': 1}
        if 'SELECT sectionname FROM public.sections' in s:
            return {'sectionname': 'A'}
        if 'RETURNING updated_at' in s:
            return {'updated_at': datetime(2026, 9, 30, 12, 0)}
        return None

    def fetchall(self):
        s = self.last
        if 'SELECT subjectcode, daydesc, starttimeid' in s:
            return [dict(r) for r in self.draft]
        if 'DISTINCT UPPER(cs.subjectcode)' in s:
            return [dict(r) for r in self.official]
        if 'DISTINCT UPPER(las.subjectcode)' in s:
            return [dict(r) for r in self.local]
        return []

    def close(self):
        pass


class _PublishConn(_Conn):
    def commit(self):
        pass

    def rollback(self):
        pass


def _publish(monkeypatch, *, cand_code, cand_fac, cand_room=1, other_code, other_fac,
             other_room=2, other_section=2, other_section_name='B', cfg, source='Official'):
    arr = {'arrangementid': 5, 'status': 'Draft', 'sectionid': 1, 'semesterid': 1,
           'programcode': 'BSIT', 'yearlevel': 1, 'ref_versionid': 1}
    draft = [{'subjectcode': cand_code, 'daydesc': 'Monday', 'starttimeid': 4, 'endtimeid': 7,
              'roomid': cand_room, 'faculty_employeenumber': cand_fac, 'official_sessionid': 100}]
    row = {'subjectcode': other_code, 'sectionid': other_section, 'roomid': other_room,
           'sectionname': other_section_name, 'programcode': 'BSIT'}
    if source == 'Official':
        official, local = [dict(row, employeenumber=other_fac)], []
    else:
        official, local = [], [dict(row, faculty_employeenumber=other_fac, official_sessionid=900)]
    cur = _PublishCursor(arr, draft, official, local)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _PublishConn(cur))
    monkeypatch.setattr(database, 'load_scheduler_config', lambda: cfg)
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    for name, value in {
        '_ensure_local_tables': lambda *a, **k: None,
        '_local_expected_updated_at_matches': lambda *a, **k: True,
        '_archive_local_for_inactive_semesters': lambda *a, **k: None,
        '_local_semester_is_active': lambda *a, **k: True,
        '_validate_local_protected_identity': lambda *a, **k: (True, None),
        '_validate_local_occurrence_coverage': lambda *a, **k: (True, None),
        '_validate_local_session_structure': lambda *a, **k: (True, None),
        '_validate_local_official_session_binding': lambda *a, **k: True,
        '_get_official_occurrence_faculty': lambda *a, **k: (cand_fac, True),
        '_validate_local_editable_dimensions': lambda *a, **k: (True, None),
        '_write_activity_log_tx': lambda *a, **k: None,
    }.items():
        monkeypatch.setattr(app_module, name, value)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='phase3b-test')
    res = client.post('/api/local/arrangement/5/publish', json={'reason': 'test'})
    return res.status_code, res.get_json()


def _faculty_rejected(status, body):
    if status == 200 and body.get('success'):
        return False
    assert status == 409 and 'conflicts' in body, (status, body)
    return any('Faculty' in c['dimensions'] for c in body['conflicts'])


# ── accepted by Local conflict validation => not rejected at Publish on HC10 ─

@pytest.mark.parametrize('sid,a_code,b_code,fac,cfg,expect', SCENARIOS, ids=IDS)
def test_publish_hc10_matches_local_conflict_validation(monkeypatch, sid, a_code, b_code, fac, cfg, expect):
    check_says_conflict = _local_hc10(monkeypatch, a_code, b_code, fac, cfg)
    status, body = _publish(monkeypatch, cand_code=a_code, cand_fac=fac,
                            other_code=b_code, other_fac=fac, cfg=cfg)
    publish_says_conflict = _faculty_rejected(status, body)
    assert check_says_conflict == publish_says_conflict == expect, (sid, check_says_conflict, publish_says_conflict)
    if not expect:
        assert status == 200 and body['success'] is True   # nothing else blocks it either


@pytest.mark.parametrize('source', ['Official', 'Local'])
def test_publish_accepts_nstp_shared_faculty_against_official_and_published_local(monkeypatch, source):
    status, body = _publish(monkeypatch, cand_code='NSTP101', cand_fac='F1', other_code='NSTP102',
                            other_fac='F1', cfg=NSTP_ONLY, source=source)
    assert status == 200 and body['success'] is True, body


# ── HC11 stays HC16-based, HC12 is never merge-exempt ───────────────────────

def test_publish_room_exempt_only_for_valid_merge(monkeypatch):
    status, body = _publish(monkeypatch, cand_code='NSTP101', cand_fac='F1', cand_room=7,
                            other_code='NSTP101', other_fac='F2', other_room=7, cfg=NSTP_ONLY)
    assert status == 200 and body['success'] is True, body          # valid merge shares room
    status, body = _publish(monkeypatch, cand_code='IT101', cand_fac='F1', cand_room=7,
                            other_code='IT201', other_fac='F2', other_room=7, cfg=ALL_SUBJECTS)
    assert status == 409 and body['conflicts'][0]['dimensions'] == ['Room']


def test_publish_same_section_conflict_is_never_merge_exempt(monkeypatch):
    status, body = _publish(monkeypatch, cand_code='IT101', cand_fac='F1', other_code='IT101',
                            other_fac='F1', other_section=1, other_section_name='A',
                            cfg=ALL_SUBJECTS)
    assert status == 409
    assert body['conflicts'][0]['dimensions'] == ['Section']        # HC10 exempt, HC12 not
