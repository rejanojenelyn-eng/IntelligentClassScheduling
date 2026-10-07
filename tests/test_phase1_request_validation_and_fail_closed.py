"""
Constraint-fix Phase 1: crash / fail-open bugs.

1. /api/requests/validate was bound to the helper _request_local_conflict
   (the @app.route decorator sat above the wrong function), so every call from
   the Academic Head Requests hub raised TypeError -> HTTP 500, and
   api_requests_validate itself was unreachable.
2. The make-up branch referenced `day` (only assigned in the adjustment
   branch) -> UnboundLocalError whenever the requested schedule had a section.
3. Official-schedule conflict queries in request validation had no semester
   filter, so bookings from ANY semester's Published schedule counted.
4. Three Publish-blocking cross-section checks (HC8 night limit, HC9 total
   load, HC15 cross-schedule) returned "no violations" when their DB read
   failed. Publish now fails closed; advisory callers keep the old behavior.

No real database is touched: query_db / get_db_connection are stubbed.
"""
from datetime import date, time
from pathlib import Path

import pytest

import app as app_module

APP_SRC = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
REQUEST_SEM = 7


# ── helpers ─────────────────────────────────────────────────────────────────

class _FakeQueryDb:
    """Stands in for app.query_db. Returns canned rows for the lookups the
    request validator performs and [] (no conflict) for every conflict probe,
    recording each (sql, params) so tests can assert on scoping."""

    def __init__(self, request_row):
        self.request_row = request_row
        self.calls = []

    def __call__(self, sql, args=(), one=False):
        self.calls.append((sql, list(args or [])))
        if 'SELECT isactive FROM accounts' in sql:      # app's before_request account guard
            return {'isactive': True}
        if 'FROM class_meeting_request cmr' in sql or 'FROM schedule_change_request scr' in sql:
            return self.request_row
        if sql.strip().startswith('SELECT semesterid FROM schedule WHERE scheduleid'):
            return {'semesterid': REQUEST_SEM}
        if 'SELECT sec.programyearlevelid FROM schedule sc2' in sql:
            return {'programyearlevelid': 3}
        if 'sec.programyearlevelid, pyl.programcode' in sql:   # _request_requester (Phase 5)
            return {'scheduleid': 501, 'semesterid': REQUEST_SEM, 'sectionid': 9,
                    'employeenumber': 'F1', 'subjectcode': 'IT101', 'sectionname': 'A',
                    'programyearlevelid': 3, 'programcode': 'BSIT'}
        return None if one else []

    def official_probes(self):
        """Conflict probes against Official schedule_sessions (not the
        class_meeting_request / local_arrangement probes)."""
        return [(s, p) for (s, p) in self.calls
                if 'FROM schedule_sessions ss' in s and "sv.status = 'Published'" in s]


def _client_as_academic_head(monkeypatch, fake):
    monkeypatch.setattr(app_module, 'query_db', fake)
    monkeypatch.setattr(app_module, 'load_scheduler_config', lambda: {})
    monkeypatch.setattr(app_module, '_ensure_request_tables', lambda *a, **k: None)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='phase1-test')
    return client


# ── 1. route binding ────────────────────────────────────────────────────────

def test_requests_validate_route_is_bound_to_the_validator_not_the_helper():
    endpoints = [r.endpoint for r in app_module.app.url_map.iter_rules()
                 if r.rule == '/api/requests/validate']
    assert len(endpoints) == 1
    assert app_module.app.view_functions[endpoints[0]] is app_module.api_requests_validate


def test_request_local_conflict_is_a_plain_helper_again():
    bound = {app_module.app.view_functions[r.endpoint] for r in app_module.app.url_map.iter_rules()}
    assert app_module._request_local_conflict not in bound


# ── 2 + 3. make-up / adjustment validation: no crash, semester-scoped ──────

MAKEUP_ROW = {
    'requestid': 11, 'scheduleid': 501, 'orig_sched': 501, 'employeenumber': 'F1',
    'requested_date': date(2026, 10, 5),            # a Monday
    'start_t': time(9, 0), 'end_t': time(10, 30), 'new_roomid': 12,
}
ADJUSTMENT_ROW = {
    'requestid': 21, 'scheduleid': 501, 'orig_sched': 501, 'employeenumber': 'F1',
    'new_daydesc': 'Tuesday', 'start_t': time(9, 0), 'end_t': time(10, 30), 'new_roomid': 12,
}


def test_makeup_validation_with_a_section_no_longer_crashes(monkeypatch):
    fake = _FakeQueryDb(MAKEUP_ROW)
    res = _client_as_academic_head(monkeypatch, fake).get('/api/requests/validate?type=makeup&id=11')
    body = res.get_json()
    assert res.status_code == 200, body
    assert body['success'] is True and body['all_ok'] is True
    # The cohort (program) branch ran -- the one that used to reference `day`.
    assert any('sec.programyearlevelid = %s' in s for s, _ in fake.official_probes())


def test_makeup_local_cohort_probe_uses_the_requested_dates_weekday(monkeypatch):
    fake = _FakeQueryDb(MAKEUP_ROW)
    _client_as_academic_head(monkeypatch, fake).get('/api/requests/validate?type=makeup&id=11')
    # The Local-occupancy cohort probe (_request_local_conflict's section subquery);
    # the effective Official query also mentions local_arrangement_sessions.
    local_cohort = [p for s, p in fake.calls
                    if 'FROM public.local_arrangement_sessions las' in s
                    and 'SELECT sectionid FROM public.sections' in s]
    assert len(local_cohort) == 1            # duplicated call removed
    assert 'Monday' in local_cohort[0]


@pytest.mark.parametrize('req_type,row,req_id', [
    ('makeup', MAKEUP_ROW, 11),
    ('adjustment', ADJUSTMENT_ROW, 21),
])
def test_every_official_conflict_probe_is_scoped_to_the_requests_semester(monkeypatch, req_type, row, req_id):
    fake = _FakeQueryDb(row)
    res = _client_as_academic_head(monkeypatch, fake).get(
        f'/api/requests/validate?type={req_type}&id={req_id}')
    assert res.status_code == 200, res.get_json()
    probes = fake.official_probes()
    assert len(probes) == 3                  # room, faculty, cohort
    for sql, params in probes:
        assert 'semesterid = %s' in sql, sql
        assert REQUEST_SEM in params, params


def test_request_validation_still_requires_academic_head(monkeypatch):
    fake = _FakeQueryDb(MAKEUP_ROW)
    monkeypatch.setattr(app_module, 'query_db', fake)
    monkeypatch.setattr(app_module, '_ensure_request_tables', lambda *a, **k: None)
    res = app_module.app.test_client().get('/api/requests/validate?type=makeup&id=11')
    assert res.status_code == 401


# ── 4. fail-closed cross-section checks ─────────────────────────────────────

def _broken_connection():
    raise RuntimeError('db down')


FAC_MAP = {
    'F1': {'fullname': 'Faculty One', 'designationid': 4, 'nightteachingservice': 2,
           'employeetype': {'regularload': 18, 'parttimeload': 12, 'teachingsubstitution': 0}},
}
NIGHT_ROW = {'faculty_id': 'F1', 'subject_code': 'IT101', 'day': 'Monday',
             'days_list': ['Monday'], 'start_time': '18:00', 'end_time': '19:30'}


@pytest.mark.parametrize('failure', ['raises', 'returns_none'])
def test_load_check_fails_closed_only_when_asked(monkeypatch, failure):
    monkeypatch.setattr(app_module, 'get_db_connection',
                        _broken_connection if failure == 'raises' else (lambda: None))
    args = ([NIGHT_ROW], FAC_MAP, 1)
    assert app_module._check_cross_program_faculty_loads(*args) == []
    with pytest.raises(app_module.ConstraintCheckUnavailable):
        app_module._check_cross_program_faculty_loads(*args, fail_closed=True)


@pytest.mark.parametrize('failure', ['raises', 'returns_none'])
def test_night_limit_check_fails_closed_only_when_asked(monkeypatch, failure):
    monkeypatch.setattr(app_module, 'get_db_connection',
                        _broken_connection if failure == 'raises' else (lambda: None))
    args = ([NIGHT_ROW], FAC_MAP, 1)
    assert app_module._check_designee_night_limit(*args) == []
    with pytest.raises(app_module.ConstraintCheckUnavailable):
        app_module._check_designee_night_limit(*args, fail_closed=True)


@pytest.mark.parametrize('failure', ['raises', 'returns_none'])
def test_cross_schedule_check_fails_closed_only_when_asked(monkeypatch, failure):
    monkeypatch.setattr(app_module, 'get_db_connection',
                        _broken_connection if failure == 'raises' else (lambda: None))
    rows = [{'subject_code': 'IT101', 'faculty_id': 'F1', 'room_id': 12, 'day': 'Monday',
             'days_list': ['Monday'], 'start_time': time(9, 0), 'end_time': time(10, 30)}]
    args = (rows, 'BSIT', 1, 'A', 'AY2627')
    assert app_module._check_cross_schedule_conflicts(*args) == ([], 0)
    with pytest.raises(app_module.ConstraintCheckUnavailable):
        app_module._check_cross_schedule_conflicts(*args, fail_closed=True)


def test_publish_opts_every_cross_section_check_into_fail_closed():
    approve = APP_SRC[APP_SRC.index('def api_approve_schedule'):APP_SRC.index('def api_delete_draft')]
    for fn in ('_check_cross_program_faculty_loads(', '_check_designee_night_limit(',
               '_check_cross_schedule_conflicts('):
        call = approve[approve.index(fn):]
        call = call[:call.index(')\n')]
        assert 'fail_closed=True' in call, fn
    # The dedicated handler rolls back and refuses before the generic 500 handler.
    handler = approve.index('except ConstraintCheckUnavailable')
    assert handler < approve.index('except Exception as e:', handler)
    assert 'conn.rollback()' in approve[handler:handler + 600]


def test_advisory_callers_stay_non_blocking():
    save_draft = APP_SRC[APP_SRC.index('def api_save_draft'):APP_SRC.index('def api_draft_sessions')]
    assert 'fail_closed' not in save_draft
    generate = APP_SRC[APP_SRC.index('def api_generate_schedule'):APP_SRC.index('def api_save_draft')]
    assert 'fail_closed' not in generate
