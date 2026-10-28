"""
Faculty Class Schedule > Export Schedule: the dialog's row count and the download
(active term, Published only). Read-only against the local database.
"""
import time
import uuid

import app as app_module
from conftest import requires_db

pytestmark = requires_db


def _client(username, role):
    c, sid = app_module.app.test_client(), uuid.uuid4().hex
    with c.session_transaction() as s:
        s.update(loggedin=True, username=username, role=role, sid=sid, last_seen=time.time(),
                 last_active=time.time(), must_change_password=False, account_setup_complete=True)
    app_module._track_login(sid)
    return c


def _body():
    progs = [r['programcode'] for r in app_module.query_db("SELECT DISTINCT programcode FROM program_yearlevel")]
    return {'programs': progs, 'year_levels': [1, 2, 3, 4, 5]}


def _faculty():
    row = app_module.query_db("SELECT username FROM accounts WHERE role = 'Faculty' AND isactive "
                              "ORDER BY username LIMIT 1", one=True)
    return _client(row['username'], 'Faculty')


def test_faculty_export_dialog_counts_rows():
    r = _faculty().post('/faculty/schedule/export/count', json=_body())
    assert r.status_code == 200 and 'error' not in r.get_json()
    assert isinstance(r.get_json()['count'], int)


def test_faculty_export_count_is_published_only():
    published = app_module.query_db("""
        SELECT COUNT(*) AS n FROM schedule_version WHERE status = 'Draft'""", one=True)['n']
    fac = _faculty().post('/faculty/schedule/export/count', json=_body()).get_json()['count']
    acad = _client('admin', 'Admin').post('/academic/schedule/export/count', json=_body()).get_json()['count']
    assert fac <= acad                         # Academic Head / Admin also see drafts
    if not published:
        assert fac == acad


def test_faculty_export_downloads_a_file():
    c = _faculty()
    if not c.post('/faculty/schedule/export/count', json=_body()).get_json()['count']:
        return                                 # nothing published to export in this database
    r = c.post('/faculty/schedule/export', json=dict(_body(), formats=['pdf'], filename='t'))
    assert r.status_code == 200 and r.mimetype == 'application/pdf' and r.data[:4] == b'%PDF'
