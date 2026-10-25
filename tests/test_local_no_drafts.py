"""
The Local Scheduler has no Draft step: Publish / Restore stage a Draft row and publish it
at once. A staged Draft that could not be published is discarded (endpoint below), and
any leftover still there an hour later is removed when the Local tables are ensured.
Published / Archived arrangements are never touched. No real database is touched.
"""
import inspect

import app as app_module


class _Cur:
    def __init__(self, draft_ids):
        self.draft_ids, self.executed, self._last = draft_ids, [], ''

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last = sql

    def fetchall(self):
        if 'SELECT arrangementid FROM public.local_arrangement' in self._last:
            return [{'arrangementid': i} for i in self.draft_ids]
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


def _client(monkeypatch, cur):
    conn = _Conn(cur)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: conn)
    monkeypatch.setattr(app_module, '_ensure_local_tables', lambda *a, **k: None)
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='local-test', employeenumber='AH1')
    return client, conn


def test_discard_removes_a_draft_with_its_sessions(monkeypatch):
    cur = _Cur([41])
    client, conn = _client(monkeypatch, cur)
    body = client.post('/api/local/arrangement/41/discard').get_json()
    assert body['success'] is True and conn.committed
    sqls = [s for s, _ in cur.executed]
    assert any('DELETE FROM public.local_arrangement_sessions' in s for s in sqls)
    final = next(s for s in sqls if s.startswith('DELETE FROM public.local_arrangement WHERE'))
    assert "status = 'Draft'" in final


def test_discard_refuses_published_or_archived(monkeypatch):
    cur = _Cur([])                       # the id is not a Draft
    client, conn = _client(monkeypatch, cur)
    res = client.post('/api/local/arrangement/7/discard')
    assert res.status_code == 409 and not conn.committed
    assert not any(s.lstrip().startswith('DELETE') for s, _ in cur.executed)


def test_startup_purge_is_draft_only_and_age_gated():
    sql = app_module._STALE_LOCAL_DRAFTS_PURGE_SQL
    assert "status = 'Draft'" in sql and "INTERVAL '1 hour'" in sql
    assert "'Published'" not in sql and "'Archived'" not in sql
    assert '_STALE_LOCAL_DRAFTS_PURGE_SQL' in inspect.getsource(app_module._apply_local_tables)
