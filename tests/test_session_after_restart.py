"""
A login made before the server (re)started must not come back by itself: the
landing page shows Log in (not Go to dashboard) and the dashboard asks for a login.
An open tab that checks in after the restart keeps its login.
No database needed: the session checks run before any query.
"""
import uuid

import app as app_module


def _signed_in_client():
    c, sid = app_module.app.test_client(), uuid.uuid4().hex
    with c.session_transaction() as s:
        s.update(loggedin=True, username='someone', role='Faculty', sid=sid,
                 must_change_password=False, account_setup_complete=True)
    return c, sid


def _restart(sid):
    with app_module._open_pages_lock:          # what a rerun does to the in-memory tracking
        app_module._open_pages.pop(sid, None)


def test_landing_page_is_signed_out_after_restart():
    c, sid = _signed_in_client()
    _restart(sid)
    page = c.get('/', headers={'Sec-Fetch-Mode': 'navigate'}).get_data(as_text=True)
    assert '>Log in<' in page and 'Go to dashboard' not in page
    with c.session_transaction() as s:
        assert 'loggedin' not in s


def test_dashboard_asks_for_login_after_restart():
    c, sid = _signed_in_client()
    _restart(sid)
    r = c.get('/faculty_dashboard', headers={'Sec-Fetch-Mode': 'navigate'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')


def test_open_tab_that_checks_in_after_restart_stays_signed_in(monkeypatch):
    # The made-up account is not in the database; answer the "is it active" check.
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    c, sid = _signed_in_client()
    _restart(sid)
    assert c.post('/api/session/heartbeat', json={'active': True, 'page': 'tab1'}).status_code == 200
    page = c.get('/', headers={'Sec-Fetch-Mode': 'navigate'}).get_data(as_text=True)
    assert 'Go to dashboard' in page
