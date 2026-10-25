"""
Faculty requests vs the Academic Head's Draft schedule.

  * Faculty only ever see / are checked against the Published schedule: an Official
    Draft never blocks a Make-up or Schedule Adjustment request.
  * The Academic Head's Validate reports the Draft meetings the request clashes with
    (same room, same faculty, same program/year level) as a note — all_ok is unchanged.
  * Approving needs the Academic Head to confirm removing those Draft meetings; on
    approval exactly the confirmed Draft meetings are deleted in the same transaction
    (an emptied Draft version is archived). Published rows are never touched.
  * The Official Publish run by "Approve & Update Official" skips those Draft meetings.
No real database is touched.
"""
import inspect
from datetime import date, time

import app as app_module

REQUESTER = {'scheduleid': 501, 'semesterid': 7, 'sectionid': 9, 'employeenumber': 'F1',
             'subjectcode': 'IT101', 'sectionname': 'A', 'programyearlevelid': 3,
             'programcode': 'BSIT'}
MAKEUP = {'requestid': 11, 'scheduleid': 501, 'employeenumber': 'F1',
          'requested_date': date(2026, 10, 5), 'start_t': time(9, 0), 'end_t': time(10, 30),
          'new_roomid': 12, 'roomid': 12, 'new_starttimeid': 2, 'new_endtimeid': 3}


def _draft(sessionid=901, roomid=12, fac='F2', pyl=4, code='CS201', section='C'):
    return {'sessionid': sessionid, 'versionid': 80, 'roomid': roomid, 'daydesc': 'Monday',
            'sectionid': 33, 'sectionname': section, 'programyearlevelid': pyl,
            'programcode': 'BSCS', 'subjectcode': code, 'employeenumber': fac,
            'roomname': 'R12', 'start_t': '09:00 AM', 'end_t': '10:30 AM'}


class _Db:
    """Fake query_db: the request row, the requester, the Draft probe; nothing else
    occupies the slot (the Published schedule is free)."""

    def __init__(self, drafts):
        self.drafts, self.calls = drafts, []

    def __call__(self, sql, args=(), one=False):
        self.calls.append((sql, list(args or [])))
        if 'SELECT isactive FROM accounts' in sql:
            return {'isactive': True}
        if 'FROM class_meeting_request cmr' in sql:
            return dict(MAKEUP)
        if sql.strip().startswith('SELECT semesterid FROM schedule WHERE scheduleid'):
            return {'semesterid': 7}
        if 'sec.programyearlevelid, pyl.programcode' in sql:
            return dict(REQUESTER)
        if 'FROM schedule_sessions dss' in sql:
            return [dict(d) for d in self.drafts]
        return None if one else []


def _setup(monkeypatch, db):
    monkeypatch.setattr(app_module, 'query_db', db)
    monkeypatch.setattr(app_module, 'load_scheduler_config', lambda: {'hc_merge_scope': 'nstp_only'})
    monkeypatch.setattr(app_module, '_ensure_request_tables', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_ensure_local_tables', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'write_activity_log', lambda *a, **k: None)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='draft-test', employeenumber='AH1')
    return client


def test_draft_probe_is_draft_only_semester_scoped_and_skips_own_class(monkeypatch):
    db = _Db([])
    _setup(monkeypatch, db)
    assert app_module._request_draft_conflicts('makeup', 11) == []
    sql, params = next((s, p) for s, p in db.calls if 'FROM schedule_sessions dss' in s)
    assert "dsv.status = 'Draft'" in sql and "'Published'" not in sql
    assert "dsv.source IS DISTINCT FROM 'local'" in sql and 'is_removal' in sql
    assert 'dsc.semesterid = %s' in sql and params[0] == 7 and params[1] == 'Monday'
    assert 9 in params and 'IT101' in params        # requester's own class is skipped


def test_validate_reports_draft_conflicts_without_blocking(monkeypatch):
    db = _Db([_draft(roomid=12), _draft(sessionid=902, roomid=99, fac='F1', pyl=3)])
    body = _setup(monkeypatch, db).get('/api/requests/validate?type=makeup&id=11').get_json()
    assert body['success'] is True and body['all_ok'] is True     # Drafts never block
    by_id = {d['sessionid']: d for d in body['draft_conflicts']}
    assert by_id[901]['dimensions'] == ['room']
    assert by_id[902]['dimensions'] == ['faculty', 'section']
    assert 'CS201' in by_id[901]['message']


def test_unrelated_draft_row_is_not_a_conflict(monkeypatch):
    db = _Db([_draft(roomid=99, fac='F2', pyl=4)])
    body = _setup(monkeypatch, db).get('/api/requests/validate?type=makeup&id=11').get_json()
    assert body['draft_conflicts'] == []


class _Cur:
    def __init__(self):
        self.executed, self._last = [], ''

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last = sql

    def fetchone(self):
        if 'FROM class_meeting_request WHERE requestid' in self._last:
            return {'requestid': 11, 'scheduleid': 501, 'new_starttimeid': 2, 'new_endtimeid': 3,
                    'requested_date': date(2026, 10, 5), 'new_roomid': 12, 'status': 'Pending'}
        if 'COUNT(*) AS c FROM schedule_sessions' in self._last:
            return {'c': 0}
        return None

    def fetchall(self):
        if 'DELETE FROM schedule_sessions' in self._last:
            return [{'versionid': 80}]
        return []

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur, self.committed = cur, False

    def cursor(self, **k):
        return self.cur

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _approve(monkeypatch, db, **extra):
    cur = _Cur()
    conn = _Conn(cur)
    client = _setup(monkeypatch, db)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: conn)
    monkeypatch.setattr(app_module, '_keep_faculty_after_schedule_delete', lambda *a: None)
    res = client.post('/api/requests/decide', json=dict(
        {'type': 'makeup', 'id': 11, 'decision': 'Approved', 'remarks': ''}, **extra))
    return res.status_code, res.get_json(), cur, conn


def test_approval_asks_to_confirm_draft_removal_first(monkeypatch):
    status, body, cur, conn = _approve(monkeypatch, _Db([_draft()]))
    assert status == 409 and body['code'] == 'DRAFT_CONFLICT_CONFIRM'
    assert [d['sessionid'] for d in body['draft_conflicts']] == [901]
    assert not conn.committed
    assert not any(s.lstrip().upper().startswith(('UPDATE', 'DELETE')) for s, _ in cur.executed)


def test_confirmed_approval_removes_only_the_draft_meetings(monkeypatch):
    status, body, cur, conn = _approve(monkeypatch, _Db([_draft()]), remove_draft_sessionids=[901])
    assert status == 200 and body['success'] and body['drafts_removed'] == 1 and conn.committed
    delete_sql, delete_params = next((s, p) for s, p in cur.executed if 'DELETE FROM schedule_sessions' in s)
    assert "sv.status = 'Draft'" in delete_sql and delete_params == [[901]]
    archive = [s for s, _ in cur.executed if "SET status = 'Archive'" in s]
    assert archive and "status = 'Draft'" in archive[0]       # emptied Draft version archived


def test_a_new_draft_conflict_after_confirmation_asks_again(monkeypatch):
    db = _Db([_draft(), _draft(sessionid=902)])
    status, body, _, conn = _approve(monkeypatch, db, remove_draft_sessionids=[901])
    assert status == 409 and body['code'] == 'DRAFT_CONFLICT_CONFIRM' and not conn.committed


def test_approval_without_draft_conflicts_is_unchanged(monkeypatch):
    status, body, cur, conn = _approve(monkeypatch, _Db([]))
    assert status == 200 and body['drafts_removed'] == 0 and conn.committed
    assert not any('DELETE FROM schedule_sessions' in s for s, _ in cur.executed)


def test_official_publish_skips_the_draft_meetings_being_removed():
    class _C:
        def execute(self, sql, params=None):
            self.sql, self.params = sql, params

        def fetchall(self):
            return []

    cur = _C()
    with app_module.app.test_request_context():
        app_module.g.publish_ignore_draft_sessionids = [901]
        app_module._fetch_section_sessions(cur, 'BSIT', 1, 7, draft_only=True, section_id=9)
    assert 'ss.sessionid = ANY(%s)' in cur.sql and cur.params[-1] == [901]
    with app_module.app.test_request_context():
        app_module._fetch_section_sessions(cur, 'BSIT', 1, 7, draft_only=True, section_id=9)
    assert 'ANY(%s)' not in cur.sql


def test_faculty_room_schedule_is_published_only():
    src = inspect.getsource(app_module.get_room_schedule)
    assert "session.get('role') == 'Faculty'" in src and "scheduler_mode = 'local'" in src
