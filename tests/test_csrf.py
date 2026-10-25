"""
C3: CSRF protection (app._csrf_protect / _inject_csrf, static/js/csrf.js).

Requests that should be REJECTED never reach a view, so they write nothing. Requests that
should be ACCEPTED go to views replaced by a stub (no real writes), except the read-only
ones noted below.
"""
import pytest

from conftest import requires_db

pytestmark = requires_db

JSON = {'Sec-Fetch-Mode': 'cors'}
PAGE = {'Sec-Fetch-Mode': 'navigate'}
ROLES = ('Admin', 'Academic Head', 'Faculty')


@pytest.fixture(scope='module')
def app_module():
    import app as app_module
    return app_module


def _account(app_module, role):
    row = app_module.query_db("SELECT username FROM accounts WHERE role = %s AND isactive "
                              "ORDER BY username LIMIT 1", (role,), one=True)
    if not row:
        pytest.skip(f"no active {role} account to test with")
    return row['username']


def _client(app_module, role=None, auto=False):
    """auto=False: the client sends NO token unless the test adds one."""
    c = app_module.app.test_client()
    c.csrf_auto = auto
    if role:
        with c.session_transaction() as s:
            s.update(loggedin=True, username=_account(app_module, role), role=role,
                     must_change_password=False, account_setup_complete=True)
    return c


def _token(client):
    with client.session_transaction() as s:
        return s.setdefault('_csrf_token', 'test-token-0123456789abcdef')


@pytest.fixture
def stubbed(app_module, monkeypatch):
    for endpoint in list(app_module.app.view_functions):
        if endpoint != 'static':
            monkeypatch.setitem(app_module.app.view_functions, endpoint, lambda **kw: 'VIEW REACHED')
    return app_module


def _rules(app_module):
    out = []
    with app_module.app.test_request_context():
        from flask import url_for
        for rule in app_module.app.url_map.iter_rules():
            args = {a: (1 if type(rule._converters[a]).__name__ == 'IntegerConverter' else 'x')
                    for a in rule.arguments}
            out.append((rule.endpoint, url_for(rule.endpoint, **args),
                        sorted(rule.methods - {'GET', 'HEAD', 'OPTIONS'})))
    return out


# ---- every state-changing route needs a valid token ---------------------------------

@pytest.mark.parametrize('role', ROLES)
def test_every_state_changing_route_rejects_missing_and_wrong_tokens(stubbed, role):
    acl = stubbed._acl
    client = _client(stubbed, role)
    good = _token(client)
    checked = 0
    for endpoint, url, methods in _rules(stubbed):
        if endpoint not in acl.PUBLIC_ENDPOINTS and role not in acl.allowed_roles(endpoint):
            continue           # C2 answers 403 first; covered by test_access_control
        for method in methods:
            r = client.open(url, method=method, headers=JSON, json={})
            assert r.status_code == 400 and r.get_json()['csrf_failed'] is True, (role, method, url)
            r = client.open(url, method=method, headers=dict(JSON, **{'X-CSRFToken': 'forged'}), json={})
            assert r.status_code == 400, (role, method, url)
            r = client.open(url, method=method, headers=dict(JSON, **{'X-CSRFToken': good}), json={})
            assert r.status_code == 200 and r.data == b'VIEW REACHED', (role, method, url, r.status_code)
            checked += 1
    assert checked > 10


def test_token_from_one_session_does_not_work_in_another(stubbed):
    victim, attacker = _client(stubbed, 'Academic Head'), _client(stubbed, 'Academic Head')
    _token(victim)
    with attacker.session_transaction() as s:
        s['_csrf_token'] = 'attacker-own-token-xyz'
    r = victim.post('/api/schedule/approve', json={}, headers=dict(JSON, **{'X-CSRFToken': 'attacker-own-token-xyz'}))
    assert r.status_code == 400


def test_form_field_and_beacon_body_carry_the_token(stubbed):
    client = _client(stubbed, 'Admin')
    token = _token(client)
    r = client.post('/admin/settings/add_designation', data={'csrf_token': token, 'name': 'x'}, headers=PAGE)
    assert r.status_code == 200 and r.data == b'VIEW REACHED'
    r = client.post('/admin/settings/add_designation', data={'name': 'x'}, headers=PAGE)
    assert r.status_code == 400 and b'Page Expired' in r.data             # friendly page, no details
    # navigator.sendBeacon: JSON body, no headers
    r = client.post('/api/session/page_closed', data=f'{{"page":"p1","csrf_token":"{token}"}}',
                    content_type='text/plain')
    assert r.status_code == 200 and r.data == b'VIEW REACHED'


def test_public_forms_are_protected_too(stubbed):
    client = _client(stubbed)                                               # signed out
    for url in ('/login', '/forgot-password'):
        r = client.post(url, data={'username': 'x', 'password': 'y', 'identifier': 'x'}, headers=PAGE)
        assert r.status_code == 400 and b'Page Expired' in r.data and b'Go to login' in r.data, url
        r = client.post(url, data={'csrf_token': _token(client), 'username': 'x'}, headers=PAGE)
        assert r.status_code == 200 and r.data == b'VIEW REACHED', url


def test_safe_methods_never_need_a_token(stubbed):
    client = _client(stubbed, 'Faculty')
    assert client.get('/faculty_dashboard', headers=PAGE).data == b'VIEW REACHED'


def test_c1_and_c2_still_answer_before_csrf(stubbed):
    anon = _client(stubbed)
    assert anon.post('/api/schedule/approve', json={}, headers=JSON).status_code == 401
    faculty = _client(stubbed, 'Faculty')
    assert faculty.post('/api/schedule/approve', json={}, headers=JSON).status_code == 403
    r = faculty.post('/api/schedule/approve', json={}, headers=dict(JSON, **{'X-CSRFToken': _token(faculty)}))
    assert r.status_code == 403                                             # a valid token never adds rights


# ---- data-changing GET links are now POST-only ---------------------------------------

_CONVERTED = [('/archive_employee/E1', 'Academic Head'), ('/restore_employee/1', 'Academic Head'),
              ('/admin/archive_employee/E1', 'Admin'), ('/admin/restore_employee/1', 'Admin'),
              ('/admin/delete_room/1', 'Admin'), ('/admin/delete_building/1', 'Admin'),
              ('/admin/curriculum/delete/1', 'Admin')]


@pytest.mark.parametrize('url,role', _CONVERTED)
def test_archive_restore_and_delete_cannot_be_triggered_by_get(stubbed, url, role):
    client = _client(stubbed, role)
    assert client.get(url, headers=PAGE).status_code == 405        # e.g. <img src="...">: refused, nothing runs
    assert client.post(url, headers=PAGE).status_code == 400       # POST without the token: refused
    r = client.post(url, data={'csrf_token': _token(client)}, headers=PAGE)
    assert r.status_code == 200 and r.data == b'VIEW REACHED'      # the page's own button (csrfPost)


def test_no_get_route_is_left_that_archives_restores_or_deletes(app_module):
    for rule in app_module.app.url_map.iter_rules():
        if 'GET' in rule.methods:
            assert not any(w in rule.rule for w in ('archive_employee', 'restore_employee', '/delete')), rule.rule


def test_pages_post_instead_of_linking_to_archive_restore_delete():
    from pathlib import Path
    js = Path(__file__).resolve().parent.parent / 'static' / 'js'
    for f in ('ACAD HEAD/employee.acad.js', 'ACAD HEAD/archivedEmployee.acad.js', 'ADMIN/employee.admin.js',
              'ADMIN/archivedEmployees.admin.js', 'ADMIN/curriculum.admin.js'):
        src = (js / f).read_text(encoding='utf-8')
        assert 'csrfPost(' in src, f
        for bad in ('.href = `/archive_employee', '.href = `/restore_employee', '.href = `/admin/archive_employee',
                    '.href = `/admin/restore_employee', 'location.href = "/admin/curriculum/delete'):
            assert bad not in src, (f, bad)


# ---- every page gets the token and csrf.js ------------------------------------------

def test_every_html_page_carries_the_token_before_its_scripts(app_module):
    pages = [(None, '/login'), (None, '/forgot-password'), (None, '/'),
             ('Faculty', '/faculty_dashboard'), ('Academic Head', '/schedule/manual-editor'),
             ('Admin', '/admin/settings')]
    for role, url in pages:
        client = _client(app_module, role)
        html = client.get(url, headers=PAGE).data.decode()
        token = _token(client)
        head = html.lower().index('<head')
        meta = html.index(f'<meta name="csrf-token" content="{token}">')
        assert head < meta < html.index('/static/js/csrf.js') < html.lower().index('</head>'), url
        assert html.count('name="csrf-token"') == 1, url
        # csrf.js is the page's FIRST script, so it is in place before any page script runs
        assert html.lower().index('<script') == html.index('<script src="/static/js/csrf.js'), url


def test_session_cookie_is_samesite_lax_and_httponly(app_module):
    client = _client(app_module)
    r = client.get('/login', headers=PAGE)
    cookie = next(v for k, v in r.headers.items() if k == 'Set-Cookie' and v.startswith('session='))
    assert 'SameSite=Lax' in cookie and 'HttpOnly' in cookie
    assert app_module.app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'


def test_secure_cookie_turns_on_for_railway_and_stays_off_locally(monkeypatch):
    import importlib
    import config
    for env, expected in [({}, False), ({'RAILWAY_ENVIRONMENT': 'production'}, True),
                          ({'RAILWAY_ENVIRONMENT_NAME': 'production'}, True),
                          ({'RAILWAY_ENVIRONMENT': 'production', 'SESSION_COOKIE_SECURE': '0'}, False),
                          ({'SESSION_COOKIE_SECURE': '1'}, True)]:
        for k in ('RAILWAY_ENVIRONMENT', 'RAILWAY_ENVIRONMENT_NAME', 'SESSION_COOKIE_SECURE'):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert importlib.reload(config).Config.SESSION_COOKIE_SECURE is expected, env
    monkeypatch.undo()
    importlib.reload(config)
