"""
Local Scheduler Save / Publish / Restore / Deactivate — backend contract.

Runs the REAL Flask endpoints against a scripted fake cursor that records every SQL
statement. Covers the contract the Manual Editor depends on:
  - Save uses top-level context keys and creates an inactive Draft only.
  - Every response a caller needs for a later mutation carries updated_at, in the
    exact format _local_expected_updated_at_matches compares against.
  - The optimistic-concurrency check is still enforced (stale / missing → 409).
  - No Local action writes Official schedule_version rows.
"""
from datetime import datetime

import pytest

import app

DT_SAVED = datetime(2026, 9, 28, 9, 15, 30, 123456)
DT_PUBLISHED = datetime(2026, 9, 28, 9, 20, 1, 654321)
DT_RESTORED = datetime(2026, 9, 28, 9, 25, 7, 111111)


class _Log(list):
    """SQL statements (normalized) plus their (statement, params) pairs."""
    def __init__(self):
        super().__init__()
        self.params = []


class _Cur:
    def __init__(self, script, log):
        self.script, self.log = script, log
        self._rows, self.rowcount = [], 0

    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        self.log.append(s)
        self.log.params.append((s, params))
        for needle, rows in self.script:
            if needle in s:
                self._rows = list(rows)
                self.rowcount = len(self._rows)
                return
        self._rows, self.rowcount = [], 0

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _Conn:
    def __init__(self, script, log):
        self.script, self.log = script, log

    def cursor(self, cursor_factory=None):
        return _Cur(self.script, self.log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def local_env(monkeypatch):
    """Stub the DB-backed validators (their own tests cover them) and log in."""
    monkeypatch.setattr(app, '_ensure_local_tables', lambda cur=None: None)
    monkeypatch.setattr(app, '_get_semester_id', lambda cur, ay, term: 1)
    monkeypatch.setattr(app, '_local_semester_is_active', lambda cur, sem: True)
    monkeypatch.setattr(app, '_archive_local_for_inactive_semesters', lambda cur: 0)
    monkeypatch.setattr(app, '_validate_local_protected_identity', lambda *a, **k: (True, None))
    monkeypatch.setattr(app, '_validate_local_session_structure', lambda *a, **k: (True, None))
    monkeypatch.setattr(app, '_validate_local_official_session_binding', lambda *a, **k: True)
    monkeypatch.setattr(app, '_get_official_occurrence_faculty', lambda *a, **k: ('E1', True))
    monkeypatch.setattr(app, '_validate_local_editable_dimensions', lambda *a, **k: (True, None))
    monkeypatch.setattr(app, '_validate_local_occurrence_coverage', lambda *a, **k: (True, None))
    monkeypatch.setattr(app, '_write_activity_log_tx', lambda *a, **k: None)
    # _enforce_account_setup re-checks the account on every request.
    monkeypatch.setattr(app, 'query_db', lambda *a, **k: {'isactive': True})

    log = _Log()

    def use(script):
        monkeypatch.setattr(app, 'get_db_connection', lambda: _Conn(script, log))

    client = app.app.test_client()
    with client.session_transaction() as sess:
        sess['loggedin'] = True
        sess['role'] = 'Academic Head'
        sess['username'] = 'tester'
    return client, use, log


def _no_official_writes(log):
    for s in log:
        assert not s.startswith('UPDATE public.schedule_version'), s
        assert not s.startswith('UPDATE schedule_version'), s
        assert not s.startswith('INSERT INTO public.schedule_version'), s


SESSION_ROW = {'subjectcode': 'COMP 001', 'daydesc': 'Monday', 'starttimeid': 19,
               'endtimeid': 23, 'roomid': 5, 'faculty_employeenumber': 'E1',
               'official_sessionid': 900}

SAVE_SCRIPT = [
    ('SELECT 1 FROM public.sections sec', [{'x': 1}]),
    ('SELECT DISTINCT sv.versionid', [{'versionid': 7}]),
    ('SELECT UPPER(cs.subjectcode) AS subjectcode, s.employeenumber',
     [{'subjectcode': 'COMP 001', 'employeenumber': 'E1'}]),
    ("INSERT INTO public.local_arrangement (programcode",
     [{'arrangementid': 41, 'updated_at': DT_SAVED}]),
]

SAVE_PAYLOAD = {
    'program': 'DIT', 'year_level': 1, 'ay_id': 'AY2627', 'term': 'A', 'section_id': 552,
    'sessions': [{'subject_code': 'COMP 001', 'day': 'Monday', 'starttimeid': 19,
                  'endtimeid': 23, 'roomid': 5, 'official_sessionid': 900}],
    'reason': 'Room swap',
}


# ── Save ────────────────────────────────────────────────────────────────────

def test_save_creates_inactive_draft_and_returns_updated_at(local_env):
    client, use, log = local_env
    use(SAVE_SCRIPT)
    resp = client.post('/api/local/save_arrangement', json=SAVE_PAYLOAD)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body['status'] == 'Draft'
    assert body['arrangement_id'] == 41
    assert body['updated_at'] == DT_SAVED.isoformat()

    insert = next(s for s in log if s.startswith('INSERT INTO public.local_arrangement (programcode'))
    assert "FALSE, 'Draft'" in insert                  # inactive Draft, never Published
    assert not any("SET status = 'Published'" in s for s in log)
    _no_official_writes(log)


def test_save_duplicate_draft_returns_its_updated_at(local_env):
    client, use, log = local_env
    use([("WHERE status = 'Draft' AND draft_fingerprint",
          [{'arrangementid': 40, 'updated_at': DT_SAVED}])] + SAVE_SCRIPT)
    body = client.post('/api/local/save_arrangement', json=SAVE_PAYLOAD).get_json()
    assert body['duplicate'] is True
    assert body['arrangement_id'] == 40
    assert body['updated_at'] == DT_SAVED.isoformat()


def test_save_rejects_the_old_nested_context_payload(local_env):
    client, use, log = local_env
    use(SAVE_SCRIPT)
    nested = {'context': {'program': 'DIT', 'yearLevel': 1, 'sectionId': 552,
                          'term': 'A', 'acadYear': 'AY2627'},
              'sessions': SAVE_PAYLOAD['sessions'], 'reason': 'x'}
    resp = client.post('/api/local/save_arrangement', json=nested)
    assert resp.status_code == 400


# ── Publish ─────────────────────────────────────────────────────────────────

def _publish_script():
    arr = {'arrangementid': 41, 'programcode': 'DIT', 'yearlevel': 1, 'sectionid': 552,
           'semesterid': 1, 'status': 'Draft', 'ref_versionid': 7, 'updated_at': DT_SAVED,
           'has_hc_violation': False, 'override_reason': None}
    return [
        ('SELECT * FROM public.local_arrangement WHERE arrangementid', [arr]),
        ('SELECT sv.versionid FROM schedule_version sv', [{'versionid': 7}]),
        ('FROM public.local_arrangement_sessions WHERE arrangementid', [dict(SESSION_ROW)]),
        ("SET status = 'Published'", [{'updated_at': DT_PUBLISHED}]),
    ]


def test_publish_with_current_timestamp_succeeds_and_returns_new_updated_at(local_env):
    client, use, log = local_env
    use(_publish_script())
    resp = client.post('/api/local/arrangement/41/publish',
                       json={'reason': 'Approved', 'expected_updated_at': DT_SAVED.isoformat()})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body['status'] == 'Published'
    assert body['updated_at'] == DT_PUBLISHED.isoformat()   # client must replace its stored value
    publish = next(s for s in log if "SET status = 'Published'" in s)
    assert "AND status = 'Draft'" in publish                 # only an explicit Draft transition
    _no_official_writes(log)


@pytest.mark.parametrize('expected', [None, '2026-01-01T00:00:00', DT_PUBLISHED.isoformat()])
def test_publish_with_stale_or_missing_timestamp_is_rejected(local_env, expected):
    client, use, log = local_env
    use(_publish_script())
    payload = {'reason': 'Approved'}
    if expected is not None:
        payload['expected_updated_at'] = expected
    resp = client.post('/api/local/arrangement/41/publish', json=payload)
    assert resp.status_code == 409
    assert resp.get_json()['code'] == 'STALE_LOCAL_ARRANGEMENT'
    assert not any("SET status = 'Published'" in s for s in log)


# ── Restore ─────────────────────────────────────────────────────────────────

def _restore_script():
    src = {'arrangementid': 30, 'programcode': 'DIT', 'yearlevel': 1, 'sectionid': 552,
           'semesterid': 1, 'status': 'Archived', 'updated_at': DT_SAVED,
           'archive_reason': 'Manually archived by Academic Head'}
    return [
        ('SELECT * FROM public.local_arrangement WHERE arrangementid', [src]),
        ('FROM public.local_arrangement_sessions WHERE arrangementid', [dict(SESSION_ROW)]),
        ('SELECT sv.versionid FROM schedule_version sv', [{'versionid': 7}]),
        ('INSERT INTO public.local_arrangement (description',
         [{'arrangementid': 42, 'updated_at': DT_RESTORED}]),
    ]


def test_restore_returns_new_draft_updated_at(local_env):
    client, use, log = local_env
    use(_restore_script())
    resp = client.post('/api/local/arrangement/30/restore',
                       json={'expected_updated_at': DT_SAVED.isoformat()})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body['new_arrangementid'] == 42
    assert body['updated_at'] == DT_RESTORED.isoformat()
    _no_official_writes(log)


def test_restore_with_stale_timestamp_is_rejected(local_env):
    client, use, log = local_env
    use(_restore_script())
    resp = client.post('/api/local/arrangement/30/restore',
                       json={'expected_updated_at': '2026-01-01T00:00:00'})
    assert resp.status_code == 409
    assert resp.get_json()['code'] == 'STALE_LOCAL_ARRANGEMENT'
    assert not any(s.startswith('INSERT INTO public.local_arrangement') for s in log)


# ── Deactivate ──────────────────────────────────────────────────────────────

def test_deactivate_reads_updated_at_so_the_check_can_pass(local_env):
    client, use, log = local_env
    use([
        ('SELECT status, is_active, archive_reason, updated_at',
         [{'status': 'Published', 'is_active': True, 'archive_reason': None, 'updated_at': DT_SAVED}]),
        ("archive_reason = 'Manually archived by Academic Head'", [{'updated_at': DT_PUBLISHED}]),
    ])
    resp = client.post('/api/local/arrangement/41/deactivate',
                       json={'expected_updated_at': DT_SAVED.isoformat()})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body['updated_at'] == DT_PUBLISHED.isoformat()


# ── List / detail ───────────────────────────────────────────────────────────

LIST_ROW = {
    'arrangementid': 41, 'description': None, 'programcode': 'DIT', 'yearlevel': 1,
    'sectionid': 552, 'sectionname': 'DIT1', 'semesterid': 1, 'semestertype': 'A',
    'academicyearid': 'AY2627', 'yearstart': 2026, 'yearend': 2027,
    'has_hc_violation': False, 'override_reason': None, 'archive_reason': None,
    'restored_from_arrangementid': None, 'archived_at': None, 'session_count': 1,
    'is_active': False, 'status': 'Draft', 'created_by': 'tester',
    'created_at': DT_SAVED, 'updated_at': DT_SAVED,
}


def test_arrangement_list_returns_updated_at(local_env):
    client, use, log = local_env
    use([('FROM public.local_arrangement la', [dict(LIST_ROW)])])
    body = client.get('/api/local/arrangements').get_json()
    assert body['arrangements'][0]['updated_at'] == DT_SAVED.isoformat()


def test_arrangement_detail_returns_updated_at(local_env):
    client, use, log = local_env
    use([('FROM public.local_arrangement la', [dict(LIST_ROW)]),
         ('FROM public.local_arrangement_sessions las', [])])
    body = client.get('/api/local/arrangement/41').get_json()
    assert body['arrangement']['updated_at'] == DT_SAVED.isoformat()


def test_updated_at_serialization_round_trips_through_the_concurrency_check():
    iso = app._local_updated_at_iso(DT_SAVED)
    assert app._local_expected_updated_at_matches({'updated_at': DT_SAVED}, iso)
    assert not app._local_expected_updated_at_matches({'updated_at': DT_SAVED}, None)


def test_save_endpoint_logs_unexpected_exceptions():
    import inspect
    src = inspect.getsource(app.api_save_local_arrangement)
    tail = src[src.index('    except Exception:\n        import traceback'):]
    assert 'traceback.print_exc()' in tail
    assert '_safe_local_api_error()' in tail


# ── Partial override: occurrence-set validation ────────────────────────────

class _IdCur:
    """Answers the snapshot membership query with a fixed set of current IDs."""
    def __init__(self, current_ids):
        self.current_ids, self.calls = set(current_ids), []

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))
        self._rows = [{'sessionid': i} for i in params[0] if i in self.current_ids]

    def fetchall(self):
        return self._rows


SNAPSHOT_477 = range(2271, 2283)        # 12 Official occurrences of BSA 2


def test_partial_override_of_one_occurrence_is_valid():
    cur = _IdCur(SNAPSHOT_477)
    ok, err = app._validate_local_occurrence_coverage(cur, 1837, 477, [{'official_sessionid': 2271}])
    assert ok, err
    sql, params = cur.calls[0]
    assert params == ([2271], 1837, 477)             # only the submitted ID is checked
    assert 'ss.sessionid = ANY(%s)' in sql and 's.sectionid = %s' in sql


def test_one_meeting_of_a_multi_occurrence_subject_is_valid():
    ok, _ = app._validate_local_occurrence_coverage(_IdCur(SNAPSHOT_477), 1837, 477,
                                                    [{'official_sessionid': 2272}])
    assert ok


@pytest.mark.parametrize('sessions, fragment', [
    ([], 'at least one Official occurrence'),
    ([{'official_sessionid': None}], 'must reference the Official occurrence'),
    ([{'official_sessionid': 2271}, {'official_sessionid': 2271}], 'more than once'),
    ([{'official_sessionid': 2255}], 'not part of the current'),        # other section
    ([{'official_sessionid': 999999}], 'not part of the current'),      # nonexistent
])
def test_invalid_occurrence_sets_are_rejected(sessions, fragment):
    ok, err = app._validate_local_occurrence_coverage(_IdCur(SNAPSHOT_477), 1837, 477, sessions)
    assert not ok and fragment in err


# ── Save: carry forward the Published Local overrides ──────────────────────

CARRY_ROWS = [
    {'subjectcode': 'ACCO 202', 'daydesc': 'Tuesday', 'starttimeid': 19, 'endtimeid': 23,
     'roomid': 5, 'faculty_employeenumber': 'E2', 'official_sessionid': 2272},
    {'subjectcode': 'COMP 001', 'daydesc': 'Friday', 'starttimeid': 1, 'endtimeid': 5,
     'roomid': 5, 'faculty_employeenumber': 'E1', 'official_sessionid': 900},   # resubmitted now
]


def _inserted_occurrences(log):
    return sorted(p[7] for s, p in log.params
                  if s.startswith('INSERT INTO public.local_arrangement_sessions'))


def test_save_inserts_only_submitted_occurrence_when_nothing_is_published(local_env):
    client, use, log = local_env
    use(SAVE_SCRIPT)
    assert client.post('/api/local/save_arrangement', json=SAVE_PAYLOAD).status_code == 200
    assert _inserted_occurrences(log) == [900]


def test_save_carries_forward_published_local_overrides_it_does_not_replace(local_env):
    client, use, log = local_env
    use([("AND la.status = 'Published' AND la.is_active = TRUE AND la.ref_versionid = %s",
          CARRY_ROWS)] + SAVE_SCRIPT)
    assert client.post('/api/local/save_arrangement', json=SAVE_PAYLOAD).status_code == 200
    assert _inserted_occurrences(log) == [900, 2272]          # 900 from payload, not the old copy
    carried = next(p for s, p in log.params if 'la.ref_versionid = %s' in s)
    assert carried == ('DIT', 1, 552, 1, 7)                   # same section + current snapshot only
    _no_official_writes(log)


# ── Editor loader: Local rows + untouched Official occurrences ─────────────

def test_editor_subject_load_keeps_untouched_official_occurrences(local_env, monkeypatch):
    client, use, log = local_env
    local_2272 = {'official_sessionid': 2272, 'daydesc': 'Tuesday', 'starttimeid': 19, 'versionid': None,
                  'subjectcode': 'ACCO 202', 'status': 'Published'}
    official = [{'sessionid': 2272, 'daydesc': 'Monday', 'starttimeid': 19, 'versionid': 1830,
                 'subjectcode': 'ACCO 202', 'status': 'Published'},
                {'sessionid': 2273, 'daydesc': 'Thursday', 'starttimeid': 19, 'versionid': 1830,
                 'subjectcode': 'ACCO 202', 'status': 'Published'}]

    def fake_query(sql, args=(), one=False):
        if 'FROM accounts' in sql:
            return {'isactive': True}
        if 'local_arrangement_sessions las' in sql:
            return [dict(local_2272)]
        if "sv.status = 'Published'" in sql:
            return [dict(r) for r in official]
        return []
    monkeypatch.setattr(app, 'query_db', fake_query)
    body = client.get('/api/manual/existing_sessions', query_string=dict(
        subject_code='ACCO 202', ay_id='AY2627', semester='A', program='BSA', year_level='2',
        section_id='477', scheduler_mode='local')).get_json()
    got = sorted((s.get('official_sessionid') or s.get('sessionid'), s['daydesc']) for s in body['sessions'])
    assert got == [(2272, 'Tuesday'), (2273, 'Thursday')]     # Official Monday replaced, Thursday kept


# ── List: Drafts are visible ───────────────────────────────────────────────

def test_arrangement_list_includes_inactive_drafts(local_env):
    client, use, log = local_env
    use([('FROM public.local_arrangement la', [dict(LIST_ROW)])])
    body = client.get('/api/local/arrangements', query_string={'status': 'Draft'}).get_json()
    assert body['arrangements'][0]['status'] == 'Draft'
    listing = next(s for s in log if 'FROM public.local_arrangement la' in s)
    assert "(la.status = 'Draft' OR la.is_active = TRUE)" in listing
    assert 'la.is_active = TRUE AND' not in listing.replace("OR la.is_active = TRUE", '')


def test_editable_dimension_check_reads_existing_faculty_columns():
    import inspect
    src = inspect.getsource(app._validate_local_editable_dimensions)
    assert 'f.facultytype' not in src
    assert 'SELECT f.employeetypeid, f.designationid' in src


def test_conflict_check_never_returns_raw_exception_text():
    import inspect
    src = inspect.getsource(app.api_local_check_room_conflicts)
    tail = src[src.rindex('except Exception'):]
    assert 'str(exc)' not in tail and '_safe_local_api_error()' in tail
