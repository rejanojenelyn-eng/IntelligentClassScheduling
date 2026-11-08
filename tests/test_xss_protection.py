"""
Stored XSS.

  * Input: every POST/PUT/PATCH/DELETE text value (form fields, JSON bodies at any depth)
    and the text inside uploaded CSV/XLSX/DOCX files is refused when it contains an HTML
    tag (app._reject_html_markup / app._upload_problem), so no script can be stored.
    A lone "<" ("units < 3") and passwords are not affected.
  * Output: the dashboard and rooms tables HTML-escape database text before innerHTML,
    and inline onclick handlers use a JS-string escape that cannot break out
    (tests/js/xss_escape_harness.js, run in Node).
  * Jinja autoescaping is on for every template; security headers are sent.

No database needed: the request is refused before the view, or reaches a stub view.
"""
import io
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

import app as app_module

FETCH = {'Sec-Fetch-Mode': 'cors'}
PAGE = {'Sec-Fetch-Mode': 'navigate'}
ROOT = Path(__file__).resolve().parent.parent
PAYLOADS = ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '"><svg onload=alert(1)>',
            'Juan </b><b>', '<!-- x -->', '< img src=x onerror=alert(1)>']


@pytest.fixture
def client(monkeypatch):
    """Signed in as Admin; the database is never touched; every view is a stub."""
    def _no_db(*a, **k):
        raise AssertionError('reached the database')
    monkeypatch.setattr(app_module, 'get_db_connection', _no_db)
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    monkeypatch.setattr(app_module, '_sweep_expired_makeups', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_runtime_schema_ready', lambda: True)
    for ep in list(app_module.app.view_functions):
        if ep != 'static':
            monkeypatch.setitem(app_module.app.view_functions, ep, lambda **kw: 'VIEW REACHED')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Admin', username='xss-test', sid=uuid.uuid4().hex,
                 last_seen=time.time(), last_active=time.time(),
                 must_change_password=False, account_setup_complete=True)
    return c


# ── input: markup is never accepted ─────────────────────────────────────────

@pytest.mark.parametrize('payload', PAYLOADS)
def test_json_text_with_html_tags_is_refused(client, payload):
    r = client.post('/api/user/name', json={'First Name': payload, 'Last Name': 'Cruz'}, headers=FETCH)
    assert r.status_code == 400 and r.data != b'VIEW REACHED'
    assert r.get_json()['success'] is False and 'First Name' in r.get_json()['error']


def test_markup_deep_inside_a_json_payload_is_refused(client):
    body = {'schedule_data': [{'subject_code': 'IT 101', 'faculty_name': 'A'},
                              {'subject_code': 'IT 102', 'remarks': ['ok', {'note': '<img src=x onerror=1>'}]}]}
    r = client.post('/api/schedule/save-draft', json=body, headers=FETCH)
    assert r.status_code == 400 and 'note' in r.get_json()['error']


@pytest.mark.parametrize('payload', PAYLOADS)
def test_form_fields_with_html_tags_are_refused(client, payload):
    r = client.post('/admin/add_room', data={'room_name': payload, 'room_type': 'Lecture'}, headers=FETCH)
    assert r.status_code == 400 and r.data != b'VIEW REACHED'
    # a normal page form post is sent back with a message instead
    r = client.post('/admin/add_room', data={'room_name': payload}, headers=dict(PAGE, Referer='http://localhost/admin/rooms'))
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/rooms')


def test_refused_form_never_redirects_to_another_site(client):
    r = client.post('/admin/add_room', data={'room_name': '<b>x</b>'},
                    headers=dict(PAGE, Referer='https://evil.example/phish'))
    assert r.status_code == 302 and 'evil.example' not in r.headers['Location']


def test_html_in_a_json_key_is_refused(client):
    r = client.post('/api/schedule/validate', json={'<img src=x onerror=1>': 1}, headers=FETCH)
    assert r.status_code == 400


@pytest.mark.parametrize('text', ['units < 3', 'a<3', 'x <= y', 'Rm. 101 & 102', 'O\'Brien', 'JavaScript: Basics',
                                  'Dela Cruz, Juan "JC"', '5 > 3'])
def test_ordinary_text_is_still_accepted(client, text):
    r = client.post('/api/user/name', json={'First Name': text}, headers=FETCH)
    assert r.status_code == 200 and r.data == b'VIEW REACHED', text
    r = client.post('/admin/add_room', data={'room_name': text}, headers=FETCH)
    assert r.status_code == 200 and r.data == b'VIEW REACHED', text


def test_passwords_are_never_inspected(client):
    r = client.post('/api/user/password/change',
                    data={'current_password': '<abc', 'new_password': '<Secret1>', 'confirm_password': '<Secret1>'},
                    headers=FETCH)
    assert r.status_code == 200 and r.data == b'VIEW REACHED'


def test_safe_methods_are_not_filtered(client):
    assert client.get('/api/search?q=<b>', headers=FETCH).data == b'VIEW REACHED'


# ── input: markup inside uploaded files ──────────────────────────────────────

def _xlsx(cell):
    from openpyxl import Workbook
    wb = Workbook(); wb.active['A1'] = 'Employee Number'; wb.active['A2'] = cell
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _docx(text):
    from docx import Document
    d = Document(); d.add_paragraph(text)
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()


@pytest.mark.parametrize('name,make', [
    ('faculty.csv', lambda p: f'Employee Number,First Name\n2024-001,{p}\n'.encode()),
    ('faculty.xlsx', _xlsx),
    ('faculty.docx', _docx),
])
def test_uploaded_files_containing_html_tags_are_refused(client, name, make):
    r = client.post('/faculty/import/csv/analyze', headers=FETCH,
                    data={'file': (io.BytesIO(make('<img src=x onerror=alert(1)>')), name)},
                    content_type='multipart/form-data')
    assert r.status_code == 400 and 'HTML' in r.get_json()['error'], name
    r = client.post('/faculty/import/csv/analyze', headers=FETCH,
                    data={'file': (io.BytesIO(make('Juan Dela Cruz')), name)}, content_type='multipart/form-data')
    assert r.status_code == 200 and r.data == b'VIEW REACHED', name


# ── output: escaping on the pages ─────────────────────────────────────────────

def test_frontend_escape_helpers_neutralise_payloads():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    out = subprocess.run([node, str(ROOT / 'tests' / 'js' / 'xss_escape_harness.js')],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr or out.stdout
    assert 'all checks passed' in out.stdout


def test_every_static_javascript_file_still_parses():
    """An escaping edit must never leave a page script that does not even load."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    # every script a page loads (static/js/ACAD HEAD/reports.js is referenced nowhere: dead code)
    pages = ''.join(p.read_text(encoding='utf-8') for p in (ROOT / 'templates').rglob('*.html'))
    files = [str(p) for p in (ROOT / 'static' / 'js').rglob('*.js') if p.name in pages]
    assert len(files) > 30
    script = ("const fs=require('fs');let bad=[];for(const f of JSON.parse(process.argv[1])){"
              "try{new Function(fs.readFileSync(f,'utf8'))}catch(e){bad.push(f+': '+e.message)}}"
              "if(bad.length){console.error(bad.join('\\n'));process.exit(1)}console.log(fs.constructor&&'ok')")
    import json
    out = subprocess.run([node, '-e', script, json.dumps(files)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr


def test_jinja_autoescapes_every_html_template():
    env = app_module.app.jinja_env
    assert env.autoescape is True or env.autoescape('x.html')
    with app_module.app.test_request_context('/'):
        html = app_module.render_template_string('<p>{{ v }}</p>', v='<img src=x onerror=alert(1)>')
    assert '<img' not in html and '&lt;img' in html


def test_no_template_disables_autoescaping():
    """|safe only on values that are already HTML-safe: |tojson output, or JSON built with
    jinja2's htmlsafe_json_dumps (rooms_by_bldg_json). Never on raw text."""
    import re
    for p in (ROOT / 'templates').rglob('*.html'):
        src = p.read_text(encoding='utf-8')
        assert 'autoescape false' not in src, p
        for m in re.finditer(r'\{\{([^}]*)\|\s*safe\s*\}\}', src):
            expr = m.group(1).replace(' ', '')
            assert expr.endswith('|tojson') or expr == 'rooms_by_bldg_json', (p, m.group(0))


def test_room_json_in_a_script_tag_cannot_close_the_tag():
    import inspect
    src = inspect.getsource(app_module)
    assert 'rooms_by_bldg_json=_htmlsafe_json(rooms_by_bldg)' in src
    assert '</script>' not in str(app_module._htmlsafe_json({'n': '</script><script>alert(1)</script>'}))


def test_security_headers_are_sent():
    r = app_module.app.test_client().get('/login')
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert r.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert "object-src 'none'" in r.headers['Content-Security-Policy']
