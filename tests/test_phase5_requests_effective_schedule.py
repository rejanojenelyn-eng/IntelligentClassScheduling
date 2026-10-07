"""
Constraint-fix Phase 5: requests + effective schedule.

  * Request conflict checks use the Effective Schedule: Published Official minus
    occurrences superseded by an active Published Local, plus active Published
    Local (Draft/Archived Local are not operational), plus approved one-date
    make-ups where relevant. HC11 is exempt only for a valid HC16 merge, HC10
    uses the shared faculty_overlap_exempt rule, the cohort/section guard never.
  * Make-up = ONE meeting on requested_date: approval records the decision on
    the request row only -- no recurring Official session, no teaching load.
  * Schedule Adjustment = semester-long Local override of one Official occurrence.
  * Academic Head approval revalidates against the latest effective schedule.
  * Save Draft HC12 is scoped to the draft's section.
No real database is touched.
"""
from datetime import date, time
from pathlib import Path

import pytest

import app as app_module
import faculty_load

APP_SRC = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')

REQUESTER = {'scheduleid': 501, 'semesterid': 7, 'sectionid': 9, 'employeenumber': 'F1',
             'subjectcode': 'IT101', 'sectionname': 'A', 'programyearlevelid': 3,
             'programcode': 'BSIT'}
MAKEUP = {'requestid': 11, 'scheduleid': 501, 'orig_sched': 501, 'employeenumber': 'F1',
          'requested_date': date(2026, 10, 5), 'start_t': time(9, 0), 'end_t': time(10, 30),
          'new_roomid': 12, 'new_starttimeid': 2, 'new_endtimeid': 3}
ADJUST = {'requestid': 21, 'scheduleid': 501, 'orig_sched': 501, 'employeenumber': 'F1',
          'new_daydesc': 'Tuesday', 'start_t': time(9, 0), 'end_t': time(10, 30),
          'new_roomid': 12, 'official_sessionid': 700, 'official_daydesc': 'Monday',
          'official_roomid': 4, 'official_start_t': time(9, 0), 'official_end_t': time(10, 30)}


def _occ(code='IT201', fac='F2', section='B'):
    return {'subjectcode': code, 'employeenumber': fac, 'sectionid': 10, 'sectionname': section,
            'programcode': 'BSIT', 'start_t': '09:00 AM', 'end_t': '10:30 AM',
            'requested_date': date(2026, 10, 5)}


class _Db:
    """Fake query_db: routes each request-check query and returns the rows
    configured for its source ('official' | 'local' | 'makeup') and dimension
    ('room' | 'faculty' | 'cohort' | 'section')."""

    def __init__(self, request_row, occupancy=None, requester=REQUESTER):
        self.request_row, self.requester = request_row, requester
        self.occupancy = occupancy or {}
        self.calls = []

    @staticmethod
    def _dimension(sql, source):
        marks = {
            'official': [('room', 'ss.roomid = %s'), ('faculty', 'sc2.employeenumber = %s'),
                         ('cohort', 'sec.programyearlevelid = %s'), ('section', 'sc2.sectionid = %s')],
            'local': [('room', 'las.roomid = %s'), ('faculty', 'las.faculty_employeenumber = %s'),
                      ('cohort', 'SELECT sectionid FROM public.sections'), ('section', 'la.sectionid = %s')],
            'makeup': [('room', 'c2.new_roomid = %s'), ('faculty', 'sc2.employeenumber = %s'),
                       ('cohort', 'sec.programyearlevelid = %s'), ('section', 'sc2.sectionid = %s')],
        }[source]
        return next((dim for dim, m in marks if m in sql), None)

    def __call__(self, sql, args=(), one=False):
        self.calls.append((sql, list(args or [])))
        if 'SELECT isactive FROM accounts' in sql:
            return {'isactive': True}
        if 'FROM class_meeting_request cmr' in sql or 'FROM schedule_change_request scr' in sql:
            return self.request_row
        if sql.strip().startswith('SELECT semesterid FROM schedule WHERE scheduleid'):
            return {'semesterid': 7}
        if 'sec.programyearlevelid, pyl.programcode' in sql:
            return dict(self.requester) if self.requester else None
        for source, marker in (('makeup', 'FROM class_meeting_request c2'),
                               ('local', 'FROM public.local_arrangement_sessions las'),
                               ('official', 'FROM schedule_sessions ss')):
            if marker in sql:
                return [dict(r) for r in self.occupancy.get((source, self._dimension(sql, source)), [])]
        return None if one else []

    def sql_for(self, marker):
        return [s for s, _ in self.calls if marker in s]


def _setup(monkeypatch, db, cfg=None):
    monkeypatch.setattr(app_module, 'query_db', db)
    monkeypatch.setattr(app_module, 'load_scheduler_config',
                        lambda: cfg if cfg is not None else {'hc_merge_scope': 'nstp_only'})
    monkeypatch.setattr(app_module, '_ensure_request_tables', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_ensure_local_tables', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'write_activity_log', lambda *a, **k: None)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='phase5-test', employeenumber='AH1')
    return client


def _validate(monkeypatch, db, req_type, req_id, cfg=None):
    body = _setup(monkeypatch, db, cfg).get(f'/api/requests/validate?type={req_type}&id={req_id}').get_json()
    assert body['success'] is True, body
    return body


# ── effective-schedule semantics of every request check ────────────────────

def test_official_probe_is_effective_published_only_and_semester_scoped(monkeypatch):
    db = _Db(MAKEUP)
    _validate(monkeypatch, db, 'makeup', 11)
    official = db.sql_for('FROM schedule_sessions ss')
    assert official, 'no Official probe ran'
    for sql in official:
        assert "sv.status = 'Published'" in sql and "'Draft'" not in sql
        assert 'sc2.semesterid = %s' in sql
        # superseded only by ACTIVE PUBLISHED Local (Draft/Archived never vacate Official)
        assert "la_x.status = 'Published' AND la_x.is_active = TRUE" in sql
        assert 'las_x.official_sessionid = ss.sessionid' in sql
    for sql in db.sql_for('FROM public.local_arrangement_sessions las'):
        assert "la.status = 'Published'" in sql and 'la.is_active = TRUE' in sql


def test_makeup_checks_other_approved_makeups_on_the_same_date_only(monkeypatch):
    db = _Db(MAKEUP, {('makeup', 'room'): [_occ()]})
    body = _validate(monkeypatch, db, 'makeup', 11)
    assert body['room_available'] is False
    sql, params = next((s, p) for s, p in db.calls if 'FROM class_meeting_request c2' in s)
    assert "c2.status = 'Approved'" in sql and 'c2.requested_date = %s' in sql
    assert date(2026, 10, 5) in params and 11 in params          # its own date; excludes itself


def test_faculty_conflict_uses_shared_hc10_rule(monkeypatch):
    db = _Db(MAKEUP, {('official', 'faculty'): [_occ(fac='F1')]})
    assert _validate(monkeypatch, db, 'makeup', 11)['faculty_free'] is False
    nstp_requester = dict(REQUESTER, subjectcode='NSTP101')
    db = _Db(MAKEUP, {('official', 'faculty'): [_occ(code='NSTP102', fac='F1')]}, nstp_requester)
    assert _validate(monkeypatch, db, 'makeup', 11)['faculty_free'] is True   # NSTP/OU shared faculty


def test_room_conflict_exempt_only_for_valid_hc16_merge(monkeypatch):
    nstp_requester = dict(REQUESTER, subjectcode='NSTP101')
    db = _Db(MAKEUP, {('official', 'room'): [_occ(code='NSTP101', fac='F2')]}, nstp_requester)
    assert _validate(monkeypatch, db, 'makeup', 11)['room_available'] is True
    db = _Db(MAKEUP, {('official', 'room'): [_occ(code='IT201', fac='F2')]})
    assert _validate(monkeypatch, db, 'makeup', 11)['room_available'] is False


def test_missing_faculty_occupant_never_blocks(monkeypatch):
    db = _Db(MAKEUP, {('local', 'faculty'): [_occ(fac=None)]})
    assert _validate(monkeypatch, db, 'makeup', 11)['faculty_free'] is True


def test_cohort_guard_is_never_merge_exempt(monkeypatch):
    nstp_requester = dict(REQUESTER, subjectcode='NSTP101')
    db = _Db(MAKEUP, {('official', 'cohort'): [_occ(code='NSTP101', fac='F1')]}, nstp_requester)
    body = _validate(monkeypatch, db, 'makeup', 11)
    assert body['program_ok'] is False and 'REQUEST_COHORT_GUARD' in body['constraint_rules']


def test_adjustment_vacates_only_the_moved_occurrence_and_its_old_override(monkeypatch):
    db = _Db(ADJUST)
    _validate(monkeypatch, db, 'adjustment', 21)
    room_official = next(p for s, p in db.calls
                         if 'FROM schedule_sessions ss' in s and 'ss.roomid = %s' in s)
    assert 700 in room_official                          # ss.sessionid <> official occurrence
    local_room = next((s, p) for s, p in db.calls
                      if 'FROM public.local_arrangement_sessions las' in s and 'las.roomid = %s' in s)
    assert 'las.official_sessionid IS DISTINCT FROM %s' in local_room[0] and 700 in local_room[1]


def test_adjustment_checks_future_approved_makeups_on_the_new_weekday(monkeypatch):
    db = _Db(ADJUST, {('makeup', 'faculty'): [_occ(fac='F1')]})
    assert _validate(monkeypatch, db, 'adjustment', 21)['faculty_free'] is False
    sql = next(s for s, _ in db.calls if 'FROM class_meeting_request c2' in s)
    assert 'c2.requested_date >= %s' in sql and "TO_CHAR(c2.requested_date, 'FMDay')" in sql


def test_adjustment_uses_official_values_for_unspecified_dimensions(monkeypatch):
    row = dict(ADJUST, new_daydesc=None, new_roomid=None)
    db = _Db(row)
    _validate(monkeypatch, db, 'adjustment', 21)
    room_probe = next(p for s, p in db.calls if 'FROM schedule_sessions ss' in s and 'ss.roomid = %s' in s)
    assert 'Monday' in room_probe and 4 in room_probe     # official day + room


# ── approval (decide) revalidates; make-up is one date ──────────────────────

class _Cur:
    def __init__(self, fetch):
        self.fetch, self.executed = fetch, []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        self._last = sql

    def fetchone(self):
        return self.fetch(self._last)

    def fetchall(self):
        # The section's current Local snapshot (_active_local_overrides): empty here.
        return []

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur, self.committed, self.rolled_back = cur, False, False

    def cursor(self, **k):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _decide(monkeypatch, db, req_type, req_id, fetch, cfg=None):
    cur = _Cur(fetch)
    conn = _Conn(cur)
    client = _setup(monkeypatch, db, cfg)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: conn)
    res = client.post('/api/requests/decide', json={'type': req_type, 'id': req_id,
                                                    'decision': 'Approved', 'remarks': ''})
    return res.status_code, res.get_json(), cur, conn


def _makeup_fetch(sql):
    return {'requestid': 11, 'scheduleid': 501, 'new_starttimeid': 2, 'new_endtimeid': 3,
            'requested_date': date(2026, 10, 5), 'new_roomid': 12} if 'class_meeting_request' in sql else None


def test_makeup_approval_records_one_date_decision_without_official_session(monkeypatch):
    status, body, cur, conn = _decide(monkeypatch, _Db(MAKEUP), 'makeup', 11, _makeup_fetch)
    assert status == 200 and body['success'] is True and conn.committed
    writes = [s for s in cur.executed if s.lstrip().upper().startswith(('INSERT', 'UPDATE'))]
    assert len(writes) == 1 and 'UPDATE class_meeting_request' in writes[0]
    assert not any('schedule_sessions' in s for s in cur.executed)


def test_makeup_approval_blocked_when_it_became_invalid(monkeypatch):
    db = _Db(MAKEUP, {('official', 'room'): [_occ()]})
    status, body, cur, conn = _decide(monkeypatch, db, 'makeup', 11, _makeup_fetch)
    assert status == 409 and body['code'] == 'REQUEST_NO_LONGER_VALID'
    assert conn.rolled_back and not conn.committed
    assert not any('UPDATE class_meeting_request' in s for s in cur.executed)


def _adjust_fetch(sql):
    if 'FROM schedule_change_request scr' in sql:
        return {'requestid': 21, 'scheduleid': 501, 'versionid': 88, 'official_sessionid': 700,
                'employeenumber': 'F1', 'semesterid': 7, 'sectionid': 9, 'subjectcode': 'IT101',
                'programcode': 'BSIT', 'yearlevel': 1, 'new_daydesc': 'Tuesday',
                'new_starttimeid': 2, 'new_endtimeid': 3, 'new_roomid': 12,
                'official_daydesc': 'Monday', 'official_starttimeid': 2, 'official_endtimeid': 3,
                'official_roomid': 4}
    if 'SELECT 1 FROM schedule_sessions ss' in sql:
        return {'?column?': 1}
    if 'SELECT sv.versionid, sv.version_number' in sql:      # _current_official_anchor
        return {'versionid': 88, 'version_number': 3}
    if 'RETURNING arrangementid' in sql:
        return {'arrangementid': 55}
    return None


def test_adjustment_approval_blocked_when_override_now_conflicts(monkeypatch):
    db = _Db(ADJUST, {('local', 'room'): [_occ()]})
    monkeypatch.setattr(app_module, '_validate_local_editable_dimensions', lambda *a, **k: (True, None))
    monkeypatch.setattr(app_module, '_validate_local_faculty_service_rules', lambda *a, **k: (True, None))
    status, body, cur, conn = _decide(monkeypatch, db, 'adjustment', 21, _adjust_fetch)
    assert status == 409 and body['code'] == 'REQUEST_NO_LONGER_VALID'
    assert not any('INSERT INTO public.local_arrangement' in s for s in cur.executed)


def test_adjustment_approval_blocked_by_local_scheduler_rules(monkeypatch):
    monkeypatch.setattr(app_module, '_validate_local_editable_dimensions', lambda *a, **k: (True, None))
    monkeypatch.setattr(app_module, '_validate_local_faculty_service_rules',
                        lambda *a, **k: (False, 'IT101: outside window'))
    status, body, cur, conn = _decide(monkeypatch, _Db(ADJUST), 'adjustment', 21, _adjust_fetch)
    assert status == 409 and body['code'] == 'INVALID_LOCAL_EDITABLE_DIMENSIONS'
    assert not any('INSERT INTO public.local_arrangement' in s for s in cur.executed)


def test_valid_adjustment_approval_publishes_the_local_override(monkeypatch):
    monkeypatch.setattr(app_module, '_validate_local_editable_dimensions', lambda *a, **k: (True, None))
    monkeypatch.setattr(app_module, '_validate_local_faculty_service_rules', lambda *a, **k: (True, None))
    status, body, cur, conn = _decide(monkeypatch, _Db(ADJUST), 'adjustment', 21, _adjust_fetch)
    assert status == 200 and body['success'] is True and conn.committed
    assert any('INSERT INTO public.local_arrangement_sessions' in s for s in cur.executed)
    assert not any('schedule_sessions (' in s for s in cur.executed)   # Official never mutated


# ── load, faculty-side checks, Save Draft HC12 ──────────────────────────────

def test_approved_makeup_never_enters_teaching_load():
    for sql in (faculty_load.FACULTY_SESSIONS_SQL, faculty_load._BATCH_HOURS_SQL_TMPL):
        assert 'class_meeting_request' not in sql


def test_faculty_submission_and_preview_use_the_effective_primitives():
    submit = APP_SRC[APP_SRC.index('def api_faculty_submit_request'):
                     APP_SRC.index('def api_faculty_check_request_conflicts')]
    assert '_request_official_conflict(' in submit and '_request_makeup_occupancy(' in submit
    preview = APP_SRC[APP_SRC.index('def api_faculty_check_request_conflicts'):
                      APP_SRC.index('def api_faculty_available_rooms')]
    assert '_request_official_conflict(' in preview and '_request_local_conflict(' in preview
    assert "IN ('Published','Draft')" not in preview        # Official Drafts are not operational


def test_faculty_preview_reports_effective_faculty_conflict(monkeypatch):
    db = _Db(MAKEUP, {('official', 'faculty'): [_occ(code='IT201', fac='F1')]})
    client = _setup(monkeypatch, db)
    with client.session_transaction() as s:
        s.update(role='Faculty', employeenumber='F1')
    body = client.get('/api/faculty/check_request_conflicts', query_string={
        'date': '2026-10-05', 'start_time': '09:00 AM', 'end_time': '10:30 AM',
        'schedule_id': '501'}).get_json()
    assert body['success'] is True and body['faculty_conflict'] is True
    assert 'IT201' in body['faculty_detail']


def test_save_draft_hc12_db_check_is_section_scoped():
    save = APP_SRC[APP_SRC.index('def api_save_draft'):APP_SRC.index('def api_draft_sessions')]
    q = save[save.index("SELECT UPPER(cs2.subjectcode) AS subjectcode, ss.daydesc"):]
    q = q[:q.index('existing = cur.fetchall()')]
    assert 'sc.sectionid = %s::int' in q
    assert '[program, year_level, sem_id, ctx_section_id, ctx_section_id]' in q
