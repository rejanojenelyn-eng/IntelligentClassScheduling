"""
C1 / C2: central login + role checks (app._access_gate, policy in access_control.py).

C1 tests send every method of every non-public route without a session: the gate answers
before any view or database work, so nothing is written.
C2 matrix tests replace every view function with a stub, so they check the gate's
allow/deny decision for every endpoint x role without running real (writing) views.
The remaining tests call real, read-only views as each role.
"""
import pytest

from conftest import requires_db

pytestmark = requires_db

ROLES = ('Admin', 'Academic Head', 'Faculty')
JSON = {'Sec-Fetch-Mode': 'cors'}          # a fetch() call
PAGE = {'Sec-Fetch-Mode': 'navigate'}      # the browser opening a page


@pytest.fixture(scope='module')
def app_module():
    import app as app_module
    return app_module


def _rules(app_module):
    """(endpoint, url, methods) for every route, with placeholder URL arguments."""
    out = []
    with app_module.app.test_request_context():
        from flask import url_for
        for rule in app_module.app.url_map.iter_rules():
            args = {a: (1 if type(rule._converters[a]).__name__ == 'IntegerConverter' else 'x')
                    for a in rule.arguments}
            out.append((rule.endpoint, url_for(rule.endpoint, **args),
                        sorted(rule.methods - {'HEAD', 'OPTIONS'})))
    return out


def _account(app_module, role):
    row = app_module.query_db("SELECT username FROM accounts WHERE role = %s AND isactive "
                              "ORDER BY username LIMIT 1", (role,), one=True)
    if not row:
        pytest.skip(f"no active {role} account to test with")
    return row['username']


def _client(app_module, role):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=_account(app_module, role), role=role,
                 must_change_password=False, account_setup_complete=True)
    return c


# ---- Step 1: the policy covers every route -----------------------------------------

def test_every_route_has_a_reviewed_policy(app_module):
    acl = app_module._acl
    endpoints = {e for e, _, _ in _rules(app_module)}
    missing = sorted(endpoints - acl.PUBLIC_ENDPOINTS - set(acl.ENDPOINT_ROLES))
    assert missing == [], f"routes without an access rule (denied until reviewed): {missing}"
    stale = sorted(set(acl.ENDPOINT_ROLES) - endpoints)
    assert stale == [], f"access rules for routes that no longer exist: {stale}"
    for endpoint, roles in acl.ENDPOINT_ROLES.items():
        assert roles and roles <= set(ROLES), endpoint


def test_public_routes_are_only_the_reviewed_allowlist(app_module):
    assert app_module._acl.PUBLIC_ENDPOINTS == {
        'static', 'favicon', 'index', 'login', 'logout', 'forgot_password', 'forgot_password_verify',
        'forgot_password_resend', 'forgot_password_new', 'session_page_closed', 'setup'}


def test_unreviewed_endpoint_is_denied_to_every_role(app_module, monkeypatch):
    monkeypatch.delitem(app_module._acl.ENDPOINT_ROLES, 'api_approve_schedule')
    for role in ROLES:
        r = _client(app_module, role).post('/api/schedule/approve', json={}, headers=JSON)
        assert r.status_code == 403, role


# ---- Step 2 (C1): no session -> 401 JSON / redirect to login -----------------------

def test_every_protected_route_rejects_anonymous_api_calls(app_module):
    public = app_module._acl.PUBLIC_ENDPOINTS
    client = app_module.app.test_client()
    checked = 0
    for endpoint, url, methods in _rules(app_module):
        if endpoint in public:
            continue
        for method in methods:
            r = client.open(url, method=method, headers=JSON, json={})
            assert r.status_code == 401, (method, url, r.status_code)
            assert r.get_json()['login_required'] is True, (method, url)
            checked += 1
    assert checked > 300


def test_anonymous_page_requests_redirect_to_login(app_module):
    public = app_module._acl.PUBLIC_ENDPOINTS
    client = app_module.app.test_client()
    for endpoint, url, methods in _rules(app_module):
        if endpoint in public or 'GET' not in methods:
            continue
        r = client.get(url, headers=PAGE)
        assert r.status_code == 302 and r.headers['Location'].endswith('/login'), (url, r.status_code)


def test_audit_examples_need_a_login(app_module):
    client = app_module.app.test_client()
    for method, url in [('POST', '/api/schedule/approve'), ('POST', '/api/schedule/generate'),
                        ('POST', '/api/schedule/save-draft'), ('POST', '/admin/add_room'),
                        ('POST', '/admin/curriculum/import'), ('GET', '/api/manual/faculty_info?emp_num=1'),
                        ('GET', '/api/debug_hist'), ('GET', '/api/schedule/diagnose'),
                        ('GET', '/api/faculty/teaching_assignments?emp_num=1&ay_id=1&sem=A')]:
        r = client.open(url, method=method, json={})       # no Sec-Fetch-Mode: decided by the URL / body
        assert r.status_code in (401, 302), (method, url, r.status_code)
        if r.status_code == 302:
            assert r.headers['Location'].endswith('/login')


def test_public_pages_still_open_without_a_session(app_module):
    client = app_module.app.test_client()
    for url in ('/login', '/forgot-password', '/', '/static/css/fonts.css'):
        assert client.get(url, headers=PAGE).status_code == 200, url
    assert client.get('/logout').status_code == 302
    assert client.post('/api/session/page_closed', data='{}').status_code == 204


# ---- Step 3 (C2): every endpoint x every role ---------------------------------------

@pytest.fixture
def stubbed(app_module, monkeypatch):
    """Every view replaced by a stub that answers 200 'VIEW REACHED'."""
    for endpoint in list(app_module.app.view_functions):
        if endpoint != 'static':
            monkeypatch.setitem(app_module.app.view_functions, endpoint, lambda **kw: 'VIEW REACHED')
    return app_module


@pytest.mark.parametrize('role', ROLES)
def test_role_matrix_matches_the_policy(stubbed, role):
    acl = stubbed._acl
    client = _client(stubbed, role)
    for endpoint, url, methods in _rules(stubbed):
        if endpoint in acl.PUBLIC_ENDPOINTS:
            continue
        allowed = role in acl.allowed_roles(endpoint)
        for method in methods:
            r = client.open(url, method=method, headers=JSON, json={})
            if allowed:
                assert r.status_code == 200 and r.data == b'VIEW REACHED', (role, method, url, r.status_code)
            else:
                assert r.status_code == 403 and r.get_json()['forbidden'] is True, (role, method, url)


def test_forbidden_page_is_an_error_page_with_a_way_home(stubbed):
    r = _client(stubbed, 'Faculty').get('/admin/settings', headers=PAGE)
    assert r.status_code == 403
    page = r.data.decode()
    assert 'Access Denied' in page and 'href="/faculty_dashboard"' in page


def test_faculty_cannot_use_admin_or_academic_head_operations(stubbed):
    client = _client(stubbed, 'Faculty')
    for method, url in [
            # Employee management (Academic Head's and Admin's), archive/restore, bulk import
            ('POST', '/add_employee'), ('POST', '/edit_employee'), ('POST', '/api/add_employee'),
            ('POST', '/api/edit_employee'), ('POST', '/archive_employee/1'), ('POST', '/bulk_archive'),
            ('POST', '/bulk_import'), ('POST', '/faculty/import/xlsx'), ('POST', '/restore_employee/1'),
            ('POST', '/employee/bulk_restore'), ('POST', '/admin/api/add_employee'),
            ('POST', '/admin/bulk_import'), ('POST', '/admin/restore_employee/1'),
            # Administrative setup
            ('POST', '/api/program-yearlevel/auto-setup'), ('POST', '/admin/settings/upsert_ay'),
            ('POST', '/admin/accounts/reset_password'), ('POST', '/admin/add_room'),
            ('POST', '/admin/curriculum/import'),
            # Scheduling: generate, edit, publish
            ('POST', '/api/schedule/generate'), ('POST', '/api/schedule/save-draft'),
            ('POST', '/api/schedule/approve'), ('DELETE', '/api/schedule/drafts/1'),
            ('POST', '/api/schedule/versions/1/restore'), ('POST', '/api/manual/assign_faculty'),
            ('POST', '/api/local/arrangement/1/publish'),
            # Other people's personal/employment data
            ('GET', '/api/manual/faculty_info?emp_num=1'), ('GET', '/api/faculty/teaching_assignments'),
            ('GET', '/admin/user-management'), ('GET', '/api/debug_hist')]:
        r = client.open(url, method=method, headers=JSON, json={})
        assert r.status_code == 403, (method, url, r.status_code)


def test_academic_head_keeps_scheduling_but_not_admin_settings(stubbed):
    client = _client(stubbed, 'Academic Head')
    for method, url in [('POST', '/api/schedule/generate'), ('POST', '/api/schedule/save-draft'),
                        ('POST', '/api/schedule/approve'), ('POST', '/api/schedule/validate'),
                        ('GET', '/schedule/manual-editor'), ('POST', '/api/local/arrangement/1/publish'),
                        ('POST', '/api/requests/decide'), ('GET', '/employee'), ('POST', '/api/add_employee')]:
        assert client.open(url, method=method, headers=JSON, json={}).status_code == 200, (method, url)
    for method, url in [('GET', '/admin/settings'), ('POST', '/admin/accounts/reset_password'),
                        ('POST', '/admin/settings/merge_groups'), ('GET', '/api/debug_hist'),
                        ('GET', '/faculty_dashboard')]:
        assert client.open(url, method=method, headers=JSON, json={}).status_code == 403, (method, url)


def test_admin_keeps_management_but_not_schedule_publishing(stubbed):
    client = _client(stubbed, 'Admin')
    for method, url in [('GET', '/admin/dashboard'), ('POST', '/admin/api/add_employee'),
                        ('POST', '/admin/curriculum/import'), ('POST', '/admin/settings/upsert_ay'),
                        ('POST', '/admin/add_room'), ('GET', '/admin/user-management'),
                        ('GET', '/api/dashboard/faculty_list'), ('POST', '/reports/export/x')]:
        assert client.open(url, method=method, headers=JSON, json={}).status_code == 200, (method, url)
    for method, url in [('POST', '/api/schedule/generate'), ('POST', '/api/schedule/approve'),
                        ('GET', '/faculty_dashboard'), ('GET', '/academic/my-dashboard')]:
        assert client.open(url, method=method, headers=JSON, json={}).status_code == 403, (method, url)


# ---- Real views still work for the right role ---------------------------------------

def test_each_role_still_opens_its_own_dashboard(app_module):
    for role, url in [('Admin', '/admin/dashboard'), ('Academic Head', '/academic/my-dashboard'),
                      ('Faculty', '/faculty_dashboard')]:
        r = _client(app_module, role).get(url, headers=PAGE)
        assert r.status_code == 200, (role, r.status_code)


def test_academic_head_schedule_validation_still_runs(app_module):
    r = _client(app_module, 'Academic Head').post('/api/schedule/validate', json={'schedule_data': []})
    assert r.status_code == 200 and 'violations' in r.get_json()


def test_profile_and_account_setup_still_work_for_every_role(app_module):
    for role in ROLES:
        client = _client(app_module, role)
        assert client.get('/api/user/me', headers=JSON).get_json()['success'] is True, role
        with client.session_transaction() as s:
            s['must_change_password'] = True        # e.g. right after an Admin password reset
        assert client.get('/account-setup/password', headers=PAGE).status_code == 200, role


def test_logout_ends_access(app_module):
    client = _client(app_module, 'Faculty')
    assert client.get('/api/user/me', headers=JSON).status_code == 200
    assert client.get('/logout').headers['Location'].endswith('/login')
    assert client.get('/api/user/me', headers=JSON).status_code == 401
    r = client.get('/faculty_dashboard', headers=PAGE)
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert client.post('/api/faculty/submit_request', json={}, headers=JSON).status_code == 401


def test_expired_session_cannot_use_protected_features(app_module):
    import time
    for idle in (60 * 60, 5 * 60):           # idle too long / no page checked in for too long
        client = app_module.app.test_client()
        with client.session_transaction() as s:
            s.update(loggedin=True, username=_account(app_module, 'Academic Head'), role='Academic Head',
                     must_change_password=False, account_setup_complete=True,
                     last_seen=time.time() - idle, last_active=time.time() - idle)
        r = client.post('/api/schedule/approve', json={}, headers=JSON)
        assert r.status_code == 401, idle
        r = client.get('/schedule/manual-editor', headers=PAGE)
        assert r.status_code == 302 and r.headers['Location'].endswith('/login'), idle


@pytest.mark.parametrize('method,url,allowed', [
    ('POST', '/api/schedule/approve',     {'Academic Head'}),
    ('POST', '/api/schedule/generate',    {'Academic Head'}),
    ('POST', '/api/schedule/save-draft',  {'Academic Head'}),
    ('POST', '/admin/add_room',           {'Admin'}),
    ('POST', '/admin/curriculum/import',  {'Admin'}),
    ('GET',  '/api/manual/faculty_info?emp_num=1', {'Academic Head'}),
])
def test_audit_critical_endpoints(stubbed, method, url, allowed):
    anon = stubbed.app.test_client().open(url, method=method, headers=JSON, json={})
    assert anon.status_code == 401
    for role in ROLES:
        r = _client(stubbed, role).open(url, method=method, headers=JSON, json={})
        assert r.status_code == (200 if role in allowed else 403), role


def test_no_redirect_loops_for_any_role(app_module):
    # follow_redirects raises on a redirect loop; every chain must end on a real page.
    anon = app_module.app.test_client()
    for url in ('/', '/login', '/schedule/manual-editor', '/admin/settings', '/faculty_dashboard'):
        assert anon.get(url, headers=PAGE, follow_redirects=True).status_code == 200, url
    for role, home in [('Admin', '/admin/dashboard'), ('Academic Head', '/academic/my-dashboard'),
                       ('Faculty', '/faculty_dashboard')]:
        client = _client(app_module, role)
        for url in ('/', '/login', '/forgot-password', home):
            assert client.get(url, headers=PAGE, follow_redirects=True).status_code == 200, (role, url)
        forbidden = '/admin/settings' if role != 'Admin' else '/faculty_dashboard'
        assert client.get(forbidden, headers=PAGE, follow_redirects=True).status_code == 403, role
        with client.session_transaction() as s:         # first login, setup not finished
            s.update(must_change_password=True, account_setup_complete=False)
        assert client.get(home, headers=PAGE, follow_redirects=True).status_code == 200, role


def test_faculty_pages_and_data_still_load(app_module):
    client = _client(app_module, 'Faculty')
    for url in ('/faculty_dashboard', '/faculty_schedule', '/faculty_teaching_assignment',
                '/faculty_room_schedule', '/faculty_requests'):
        assert client.get(url, headers=PAGE).status_code == 200, url
    term = app_module.query_db("SELECT academicyearid, semestertype FROM semester WHERE isactive LIMIT 1", one=True)
    if not term:
        pytest.skip('no active semester')
    emp = app_module.query_db("SELECT employeenumber FROM accounts WHERE username = %s",
                              (_account(app_module, 'Faculty'),), one=True)['employeenumber']
    for url in ('/api/faculty/me', '/api/faculty/my_teaching_load', '/api/faculty/my_requests', '/api/user/me',
                f"/api/faculty/schedule?ay_id={term['academicyearid']}&semester={term['semestertype']}"
                f"&emp_num={emp}"):
        assert client.get(url, headers=JSON).status_code == 200, url


def test_academic_head_pages_still_load(app_module):
    client = _client(app_module, 'Academic Head')
    for url in ('/academic/my-dashboard', '/dashboard', '/schedule', '/academic/schedule-generation',
                '/schedule/manual-editor', '/schedule/drafts', '/schedule/local-arrangements',
                '/employee', '/requests', '/reports', '/curriculum', '/room'):
        assert client.get(url, headers=PAGE).status_code == 200, url


def test_admin_pages_still_load(app_module):
    client = _client(app_module, 'Admin')
    for url in ('/admin/dashboard', '/admin/employee', '/admin/archived_employees', '/admin/user-management',
                '/admin/account-corrections', '/admin/settings', '/admin/program-management',
                '/admin/rooms', '/admin/curriculum', '/admin/schedule', '/admin/reports'):
        assert client.get(url, headers=PAGE).status_code == 200, url


def test_password_recovery_pages_open_without_a_session(app_module):
    client = app_module.app.test_client()
    assert client.get('/forgot-password', headers=PAGE).status_code == 200
    for url in ('/forgot-password/verify', '/forgot-password/new-password'):
        r = client.get(url, headers=PAGE, follow_redirects=True)
        assert r.status_code == 200 and b'Access Denied' not in r.data, url


def test_faculty_never_receives_draft_schedules(app_module):
    client = _client(app_module, 'Faculty')
    row = app_module.query_db("""SELECT sc.employeenumber, sem.academicyearid, sem.semestertype
                                 FROM schedule_version sv JOIN schedule sc ON sc.scheduleid = sv.scheduleid
                                 JOIN semester sem ON sem.semesterid = sc.semesterid
                                 WHERE sv.status = 'Draft' AND sc.employeenumber IS NOT NULL LIMIT 1""", one=True)
    if not row:
        pytest.skip('no Draft schedule with an instructor to test with')
    base = (f"/api/get_faculty_schedule?emp_num={row['employeenumber']}&ay_id={row['academicyearid']}"
            f"&semester={row['semestertype']}")
    with_draft = client.get(base + '&prefer_draft=1', headers=JSON).get_json()
    published = client.get(base, headers=JSON).get_json()
    assert with_draft == published          # prefer_draft is ignored for a Faculty session
