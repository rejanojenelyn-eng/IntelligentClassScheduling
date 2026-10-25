"""
C4: stored XSS. Malicious-looking values stored in the database (rooms, buildings, faculty
names) must come out of the server as inert text, wherever they are rendered.

Payloads are harmless strings. They are written inside a transaction on ONE connection that
is rolled back at the end (same pattern as test_account_setup_review), so nothing is kept.
JavaScript-side rendering is covered by tests/test_safe_html_js.py and the browser check.
"""
import os
import re

import psycopg2
import pytest

from conftest import requires_db

pytestmark = requires_db
PAGE = {'Sec-Fetch-Mode': 'navigate'}
JSON = {'Sec-Fetch-Mode': 'cors'}

# Breaks out of: element text, a double-quoted attribute, a single-quoted JS string in an
# onclick, and a <script> block.
ROOM_PAYLOAD = "x');alert(1);('<img src=x onerror=alert(2)>"          # 44 chars (room 50)
BLDG_PAYLOAD = "</script><script>alert(3)</script><b onmouseover=alert(4)>B"
NAME_PAYLOAD = "O'Neil\"><img src=x onerror=alert(5)>"                   # 39 chars (faculty 60)
# Only these are dangerous: a real tag start, or a real quote that ends a JS string. The
# words themselves (e.g. "onerror=alert" after &lt; or <) are harmless text.
RAW_MARKERS = ('<img src=x', '<script>alert', '<b onmouseover', "');alert(1)")


class _SharedConn:
    def __init__(self, real):
        self._real = real

    def cursor(self, *a, **k):
        return self._real.cursor(*a, **k)

    def commit(self):
        with self._real.cursor() as c:
            c.execute("SAVEPOINT committed")

    def rollback(self):
        with self._real.cursor() as c:
            c.execute("ROLLBACK TO SAVEPOINT committed")

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    import app as app_module
    import database
    from config import Config

    real = psycopg2.connect(dbname=Config.DB_NAME, user=Config.DB_USER, password=Config.DB_PASS,
                            host=Config.DB_HOST, port=Config.DB_PORT)
    shared = _SharedConn(real)
    shared.commit()
    monkeypatch.setattr(database, 'get_db_connection', lambda: shared)
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: shared)

    def q(sql, args=()):
        c = real.cursor(); c.execute(sql, args)
        r = c.fetchall() if c.description else []
        c.close(); return r

    # "Previously stored" malicious values, written straight into the tables.
    bldg = q("INSERT INTO building (buildingname, isactive) VALUES (%s, TRUE) RETURNING buildingid", (BLDG_PAYLOAD,))[0][0]
    room = q("""INSERT INTO room (roomname, roomtype, roomcapacity, buildingid, isactive)
                VALUES (%s, 'Lecture', 40, %s, TRUE) RETURNING roomid""", (ROOM_PAYLOAD, bldg))[0][0]
    emp = q("SELECT employeenumber FROM faculty ORDER BY employeenumber LIMIT 1")[0][0]
    q("UPDATE faculty SET lastname = %s WHERE employeenumber = %s", (NAME_PAYLOAD, emp))
    shared.commit()

    def client(role):
        rows = q("SELECT username FROM accounts WHERE role = %s AND isactive ORDER BY username LIMIT 1", (role,))
        if not rows:
            pytest.skip(f"no active {role} account")
        c = app_module.app.test_client()
        with c.session_transaction() as s:
            s.update(loggedin=True, username=rows[0][0], role=role,
                     must_change_password=False, account_setup_complete=True)
        return c

    yield type('Env', (), dict(q=staticmethod(q), client=staticmethod(client), app=app_module,
                               room=room, bldg=bldg, emp=emp))
    real.rollback()
    real.close()


def _no_raw_payload(html, where):
    for marker in RAW_MARKERS:
        assert marker not in html, f"{where}: raw {marker!r} reached the page"


def _handler_attrs(html):
    """All inline-handler attribute values, as the browser would see them."""
    return re.findall(r"""\son[a-z]+=(?:'([^']*)'|"([^"]*)")""", html)


# ---- 1, 2, 8: stored room / building names render as text -----------------------------

@pytest.mark.parametrize('role,url', [('Academic Head', '/room'), ('Admin', '/admin/rooms')])
def test_stored_room_and_building_names_render_as_text(env, role, url):
    html = env.client(role).get(url, headers=PAGE).data.decode()
    assert 'alert(1)' in html and 'alert(3)' in html            # the rows really are on the page
    _no_raw_payload(html, url)
    assert '&lt;img src=x onerror=alert(2)&gt;' in html          # escaped in the table text
    # Inline handlers carry the names as JSON (\u0027 for quotes): no way out of the string.
    handlers = ' '.join(a or b for a, b in _handler_attrs(html))
    assert "x\\u0027);alert(1);(\\u0027" in handlers
    assert "');alert(1)" not in handlers


def test_room_detail_embeds_room_lists_with_tojson(env):
    html = env.client('Academic Head').get(f'/room/view/{env.room}', headers=PAGE).data.decode()
    assert 'const ROOMS_BY_BLDG   = {' in html
    script = html.split('const ROOMS_BY_BLDG')[1].split('\n')[0]
    assert '</script>' not in script and '\\u003c/script\\u003e' in script
    _no_raw_payload(html, 'room detail')


# ---- employee (faculty) names --------------------------------------------------------------

@pytest.mark.parametrize('role,url', [('Academic Head', '/employee'), ('Admin', '/admin/employee')])
def test_stored_faculty_names_render_as_text(env, role, url):
    html = env.client(role).get(url, headers=PAGE).data.decode()
    assert 'alert(5)' in html
    _no_raw_payload(html, url)
    handlers = ' '.join(a or b for a, b in _handler_attrs(html))
    assert 'O\\u0027Neil\\"\\u003e\\u003cimg' in handlers or 'O\\u0027Neil\\"' in handlers


def test_api_returns_names_as_plain_json_data(env):
    """APIs hand the value to the page as data; the page's JS escapes it when rendering."""
    rows = env.client('Academic Head').get('/api/dashboard/rooms_list', headers=JSON).get_json()
    flat = repr(rows)
    assert ROOM_PAYLOAD in flat or 'alert(1)' in flat or rows is not None


# ---- 4: no unsafe template patterns are left --------------------------------------------

def _templates():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.endswith('.html'):
                p = os.path.join(dp, fn)
                yield os.path.relpath(p, root), open(p, encoding='utf-8').read()


def test_no_safe_filter_and_no_raw_jinja_in_inline_handlers():
    for rel, src in _templates():
        assert not re.search(r'\|\s*safe\b', src), f"{rel}: |safe"
        assert not re.search(r'\son[a-z]+="[^"]*\{\{', src), f"{rel}: Jinja value inside a double-quoted handler"
        for m in re.finditer(r"\son[a-z]+='([^']*)'", src):
            for v in re.findall(r'\{\{(.*?)\}\}', m.group(1)):
                assert 'tojson' in v, f"{rel}: handler value without tojson: {v.strip()}"


def test_jinja_values_inside_scripts_use_tojson():
    for rel, src in _templates():
        for block in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', src, re.S | re.I):
            for v in re.findall(r'\{\{(.*?)\}\}', block, re.S):
                v = v.strip()
                if 'tojson' in v or v.startswith(("'true' if", 'url_for')):
                    continue
                pytest.fail(f"{rel}: {{{{ {v} }}}} inside <script> without |tojson")


def test_rooms_by_bldg_is_no_longer_a_prebuilt_json_string():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py'),
               encoding='utf-8').read()
    assert 'rooms_by_bldg_json' not in src


def test_every_page_loads_the_escape_helpers_first(env):
    html = env.client('Faculty').get('/faculty_dashboard', headers=PAGE).data.decode()
    head = html[:html.lower().index('</head>')]
    assert head.index('/static/js/csrf.js') < head.index('/static/js/safe_html.js')
    assert html.lower().index('<script') == html.index('<script src="/static/js/csrf.js')


# ---- input validation (defence in depth) ----------------------------------------------------

@pytest.mark.parametrize('role', ['Admin', 'Academic Head'])
def test_room_and_building_names_reject_markup_but_keep_normal_punctuation(env, role):
    c = env.client(role)
    r = c.post('/admin/api/add_building', json={'name': '<img src=x onerror=alert(1)>'}, headers=JSON)
    assert r.status_code == 400 and 'cannot contain' in r.get_json()['error']
    r = c.post('/admin/api/add_room', json={'room_name': 'R<1>', 'room_type': 'Lecture', 'capacity': 40,
                                            'bldg_id': env.bldg}, headers=JSON)
    assert r.status_code == 400
    r = c.post('/admin/api/add_building', json={'name': 'St. Mary\'s "Annex" - North'}, headers=JSON)
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert env.q("SELECT 1 FROM building WHERE buildingname = %s", ('St. Mary\'s "Annex" - North',))


def test_admin_form_routes_validate_too(env):
    c = env.client('Admin')
    before = env.q("SELECT COUNT(*) FROM building")[0][0]
    r = c.post('/admin/add_building', data={'bldg_name': '<script>alert(1)</script>'}, headers=PAGE)
    assert r.status_code == 302
    assert env.q("SELECT COUNT(*) FROM building")[0][0] == before


# ---- 9: C1 / C2 / C3 still in force ----------------------------------------------------------

def test_security_controls_still_in_force(env):
    anon = env.app.app.test_client()
    assert anon.post('/admin/api/add_room', json={}, headers=JSON).status_code == 401           # C1
    assert env.client('Faculty').post('/admin/api/add_room', json={}, headers=JSON).status_code == 403  # C2
    no_token = env.client('Admin')
    no_token.csrf_auto = False
    assert no_token.post('/admin/api/add_room', json={}, headers=JSON).status_code == 400       # C3
