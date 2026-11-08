"""
Access control for every route (app._require_login + app.roles_required).

  * Anonymous visitors get 401 (fetch / API / form calls) or a redirect to /login
    (a browser opening a page) on every endpoint not in app.PUBLIC_ENDPOINTS — all
    321 routes and every HTTP method, checked by walking app.url_map.
  * A signed-in account whose role is not allowed gets 403 on every endpoint guarded
    by @roles_required.
  * Allowed roles still get through (decorator unit test + real endpoints, DB-backed).
  * Faculty can read only their own schedule through /api/get_faculty_schedule.
  * Every non-public endpoint has a recorded authorization decision, so a new route
    added without one fails this file.

Rejections happen before any view code or database work runs, so most tests need no
database: get_db_connection is replaced with one that fails the test if touched.
"""
import inspect
import re
import time
import uuid

import pytest

import app as app_module
from conftest import requires_db

FETCH = {'Sec-Fetch-Mode': 'cors'}          # what a page's fetch() sends
NAVIGATE = {'Sec-Fetch-Mode': 'navigate'}   # what opening a page sends
ROLES = ('Admin', 'Academic Head', 'Faculty')
SKIP_METHODS = {'HEAD', 'OPTIONS'}

# Signed in, any role: each acts only on the caller's own account (taken from the
# session, never from the request), is an Account Setup step, or shows published data
# every role is meant to see. Anything else must carry a role check.
LOGIN_ONLY_ENDPOINTS = {
    'session_heartbeat', 'intro_video',
    'account_setup_password', 'account_setup_photo', 'account_setup_email',
    'account_setup_contact', 'account_setup_verify', 'account_setup_done',
    'api_user_me', 'api_user_options', 'api_user_profile_update', 'api_user_photo_upload',
    'api_user_name_update', 'api_user_contact_update', 'api_user_password_eligibility',
    'api_user_password_request_code', 'api_user_password_verify_code', 'api_user_password_change',
    'api_user_email_request_code', 'api_user_email_update', 'api_user_correction_requests',
    'api_user_submit_correction_request', 'api_user_accept_employee_id',
    'api_faculty_me', 'api_faculty_my_teaching_load', 'api_faculty_my_assignments',
    'api_faculty_blocked_times', 'api_faculty_check_request_conflicts',
    'api_faculty_available_rooms', 'api_faculty_schedule_full',
    'api_get_faculty_schedule',     # Faculty: own schedule only (see the ownership tests)
    'get_room_schedule', 'api_sections_by_program', 'api_search',
}

# A role check written inside the view (the convention before roles_required existed).
_INLINE_ROLE_CHECK = re.compile(
    r"session\.get\(['\"]role['\"]\)\s*(!=|not in)|_ROOM_ROLES|_FACSUB_ROLES"
    r"|_require_academic_head_api\(\)|_mg_admin_only\(\)")


def _rules():
    return [r for r in app_module.app.url_map.iter_rules() if r.endpoint != 'static']


def _sample_url(rule):
    """The rule with every <converter:arg> filled in (1 for numbers, 'x' otherwise)."""
    return re.sub(r'<(?:(\w+)(?:\([^)]*\))?:)?\w+>',
                  lambda m: '1' if m.group(1) in ('int', 'float') else 'x', rule.rule)


def _methods(rule):
    return sorted(rule.methods - SKIP_METHODS)


@pytest.fixture
def no_db(monkeypatch):
    """Fail loudly if a request reaches the database; answer the per-request
    'is this account still active' check and skip the signed-in housekeeping hooks."""
    def _no_connection(*a, **k):
        raise AssertionError('request reached the database before being rejected')
    monkeypatch.setattr(app_module, 'get_db_connection', _no_connection)
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    monkeypatch.setattr(app_module, '_sweep_expired_makeups', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_runtime_schema_ready', lambda: True)


def _client_as(role, **extra):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role=role, username=f'test-{role}', sid=uuid.uuid4().hex,
                 last_seen=time.time(), last_active=time.time(),
                 must_change_password=False, account_setup_complete=True, **extra)
    return c


# ── 1. Anonymous visitors ────────────────────────────────────────────────────

def test_every_protected_route_rejects_anonymous_api_calls_with_401(no_db):
    client = app_module.app.test_client()
    checked, wrong = 0, []
    for rule in _rules():
        if rule.endpoint in app_module.PUBLIC_ENDPOINTS:
            continue
        for method in _methods(rule):
            resp = client.open(_sample_url(rule), method=method, headers=FETCH)
            checked += 1
            body = resp.get_json(silent=True) or {}
            if resp.status_code != 401 or body.get('success') is not False:
                wrong.append(f'{method} {rule.rule} -> {resp.status_code}')
    assert not wrong, 'reachable without logging in:\n' + '\n'.join(wrong)
    assert checked > 300          # every route x method was really exercised


def test_non_get_requests_without_fetch_headers_also_get_401(no_db):
    """e.g. curl -X POST: no Sec-Fetch-Mode header, still refused with 401."""
    client = app_module.app.test_client()
    for method, url in [('POST', '/admin/add_room'), ('POST', '/admin/add_building'),
                        ('DELETE', '/admin/settings/merge_load_policies/1'),
                        ('DELETE', '/api/schedule/drafts/1'), ('POST', '/api/schedule/generate'),
                        ('POST', '/api/schedule/versions/1/restore'),
                        ('POST', '/admin/curriculum/import'), ('POST', '/bulk_archive')]:
        assert client.open(url, method=method).status_code == 401, f'{method} {url}'


@pytest.mark.parametrize('url', ['/api/debug_hist', '/api/schedule/diagnose',
                                 '/admin/settings/merge_load_policies', '/api/subjects/all'])
def test_debug_and_data_endpoints_need_a_login(no_db, url):
    assert app_module.app.test_client().get(url).status_code in (401, 302)
    assert app_module.app.test_client().get(url, headers=FETCH).status_code == 401


@pytest.mark.parametrize('url', ['/employee', '/schedule', '/reports', '/schedule/drafts'])
def test_opening_a_protected_page_signed_out_redirects_to_login(no_db, url):
    resp = app_module.app.test_client().get(url, headers=NAVIGATE)
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/login')


def test_public_pages_still_work_signed_out():
    client = app_module.app.test_client()
    assert client.get('/login').status_code == 200
    assert client.get('/forgot-password').status_code == 200
    assert client.get('/static/js/login.js').status_code == 200
    assert client.get('/favicon.ico').status_code in (200, 204, 302, 404)   # not 401
    assert client.get('/no-such-page').status_code == 404                  # not 401


# ── 2. Signed in with the wrong role ─────────────────────────────────────────

def test_every_role_guarded_route_rejects_other_roles_with_403(no_db):
    wrong, checked = [], 0
    for rule in _rules():
        allowed = getattr(app_module.app.view_functions[rule.endpoint], 'allowed_roles', None)
        if not allowed:
            continue
        for role in ROLES:
            if role in allowed:
                continue
            client = _client_as(role)
            for method in _methods(rule):
                resp = client.open(_sample_url(rule), method=method, headers=FETCH)
                checked += 1
                if resp.status_code != 403:
                    wrong.append(f'{role}: {method} {rule.rule} -> {resp.status_code}')
    assert not wrong, 'wrong role not refused:\n' + '\n'.join(wrong)
    assert checked > 100


@pytest.mark.parametrize('method,url', [
    ('POST', '/bulk_archive'), ('POST', '/archive_employee/1'), ('POST', '/add_employee'),
    ('POST', '/api/schedule/generate'), ('POST', '/api/schedule/save-draft'),
    ('DELETE', '/api/schedule/drafts/1'), ('POST', '/reports/export/faculty'),
    ('DELETE', '/admin/settings/merge_load_policies/1'),
])
def test_faculty_cannot_use_staff_operations(no_db, method, url):
    assert _client_as('Faculty').open(url, method=method, headers=FETCH).status_code == 403


@pytest.mark.parametrize('method,url', [
    ('POST', '/api/schedule/generate'), ('POST', '/api/schedule/versions/1/restore'),
    ('POST', '/api/manual/assign_faculty'),
])
def test_admin_cannot_change_schedules(no_db, method, url):
    assert _client_as('Admin').open(url, method=method, headers=FETCH).status_code == 403


@pytest.mark.parametrize('method,url', [
    ('POST', '/admin/settings/merge_load_policies'), ('POST', '/admin/curriculum/import'),
    ('GET', '/api/debug_hist'),
])
def test_academic_head_cannot_use_admin_settings(no_db, method, url):
    assert _client_as('Academic Head').open(url, method=method, headers=FETCH).status_code == 403


def test_wrong_role_opening_a_page_is_sent_to_its_own_dashboard(no_db):
    resp = _client_as('Faculty').get('/employee', headers=NAVIGATE)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/faculty_dashboard')


# ── 3. Allowed roles still get through ───────────────────────────────────────

def test_roles_required_runs_the_view_only_for_allowed_roles():
    view = app_module.roles_required('Academic Head', 'Admin')(lambda: 'ran')
    with app_module.app.test_request_context('/api/x', headers=FETCH):
        app_module.session.update(loggedin=True, role='Academic Head')
        assert view() == 'ran'
        app_module.session['role'] = 'Admin'
        assert view() == 'ran'
        app_module.session['role'] = 'Faculty'
        assert view()[1] == 403
        app_module.session.clear()
        assert view()[1] == 401


def _real_client(role, cur, with_employee=False):
    cur.execute("""SELECT userid, username, employeenumber FROM accounts
                   WHERE isactive AND role = %s {} ORDER BY userid LIMIT 1""".format(
                    "AND employeenumber IS NOT NULL" if with_employee else ""), (role,))
    acc = cur.fetchone()
    if not acc:
        pytest.skip(f'no active {role} account in the database')
    client = _client_as(role, userid=acc['userid'])
    with client.session_transaction() as s:
        s['username'] = acc['username']
    return client, acc


@pytest.fixture
def cur():
    conn = app_module.get_db_connection()
    c = conn.cursor(cursor_factory=app_module.RealDictCursor)
    yield c
    c.close(); conn.close()


@requires_db
@pytest.mark.parametrize('role,url', [
    ('Admin', '/admin/settings/merge_load_policies'),
    ('Admin', '/api/dashboard/buildings_list'),
    ('Admin', '/api/subjects/all'),
    ('Academic Head', '/api/hc_config'),
    ('Academic Head', '/api/dashboard/rooms_list'),
    ('Academic Head', '/api/schedule/versions'),
])
def test_allowed_roles_can_still_read_their_data(cur, role, url):
    client, _ = _real_client(role, cur)
    resp = client.get(url, headers=FETCH)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]


@requires_db
def test_academic_head_can_still_open_the_employee_page(cur):
    client, _ = _real_client('Academic Head', cur)
    assert client.get('/employee', headers=NAVIGATE).status_code == 200


# ── 4. Ownership: a faculty member's own schedule only ───────────────────────

@pytest.mark.parametrize('query', ['emp_num=SOMEONE-ELSE', '',
                                   'emp_num=F-OWN&ay_id=AY1&semester=A&prefer_draft=1'])
def test_faculty_cannot_read_other_schedules_or_drafts(no_db, query):
    client = _client_as('Faculty', employeenumber='F-OWN')
    assert client.get('/api/get_faculty_schedule?' + query, headers=FETCH).status_code == 403


@requires_db
def test_faculty_can_read_their_own_schedule_and_staff_can_read_anyones(cur):
    faculty, acc = _real_client('Faculty', cur, with_employee=True)
    resp = faculty.get(f"/api/get_faculty_schedule?emp_num={acc['employeenumber']}&official_only=1",
                       headers=FETCH)
    assert resp.status_code == 200 and isinstance(resp.get_json(), list)
    head, _ = _real_client('Academic Head', cur)
    assert head.get(f"/api/get_faculty_schedule?emp_num={acc['employeenumber']}",
                    headers=FETCH).status_code == 200


# ── 5. Every route has an authorization decision ─────────────────────────────

def test_every_route_is_public_role_guarded_or_deliberately_login_only():
    undecided = []
    for rule in _rules():
        ep = rule.endpoint
        view = app_module.app.view_functions[ep]
        if ep in app_module.PUBLIC_ENDPOINTS or getattr(view, 'allowed_roles', None):
            continue
        if ep in LOGIN_ONLY_ENDPOINTS:
            continue
        if _INLINE_ROLE_CHECK.search(inspect.getsource(inspect.unwrap(view))):
            continue
        undecided.append(f'{ep} ({rule.rule})')
    assert not undecided, ('routes without a role decision — add @roles_required(...) or, if '
                           'any signed-in account may use it, LOGIN_ONLY_ENDPOINTS:\n'
                           + '\n'.join(undecided))
