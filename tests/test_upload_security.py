"""
File upload security (app._validate_uploads / app._upload_problem / MAX_CONTENT_LENGTH).

Every uploaded file is checked before any view parses it:
  * only the types the app imports: CSV, XLSX (and legacy XLS), DOCX, PDF, JPG, PNG;
  * the content must really be that type (signature / Office structure), not just the name;
  * an XLSX/DOCX may not expand into a zip bomb;
  * the whole request is limited to app.MAX_UPLOAD_BYTES (413 with a readable message).
Refused uploads never reach the view (stubbed here), so nothing is parsed or stored.
"""
import io
import time
import uuid

import pytest

import app as app_module

FETCH = {'Sec-Fetch-Mode': 'cors'}
URL = '/admin/curriculum/import'          # any upload endpoint: the check is global


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, 'get_db_connection', lambda *a, **k: (_ for _ in ()).throw(AssertionError('db')))
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    monkeypatch.setattr(app_module, '_sweep_expired_makeups', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_runtime_schema_ready', lambda: True)
    for ep in list(app_module.app.view_functions):
        if ep != 'static':
            monkeypatch.setitem(app_module.app.view_functions, ep, lambda **kw: 'VIEW REACHED')
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, role='Admin', username='upload-test', sid=uuid.uuid4().hex,
                 last_seen=time.time(), last_active=time.time(),
                 must_change_password=False, account_setup_complete=True)
    return c


def _post(client, data, name, field='file'):
    return client.post(URL, headers=FETCH, content_type='multipart/form-data',
                       data={field: (io.BytesIO(data), name), 'program_code': 'BSIT'})


def _xlsx():
    from openpyxl import Workbook
    wb = Workbook(); wb.active['A1'] = 'Course Code'; wb.active['A2'] = 'IT 101'
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _docx():
    from docx import Document
    d = Document(); d.add_paragraph('Curriculum BSIT 2024')
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()


def _png():
    from PIL import Image
    buf = io.BytesIO(); Image.new('RGB', (4, 4), 'red').save(buf, 'PNG'); return buf.getvalue()


def _jpg():
    from PIL import Image
    buf = io.BytesIO(); Image.new('RGB', (4, 4), 'red').save(buf, 'JPEG'); return buf.getvalue()


def _zip(entries):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
EXE = b'MZ\x90\x00\x03\x00\x00\x00' + b'\x00' * 200


@pytest.mark.parametrize('name,data', [
    ('curriculum.csv', b'Course Code,Title\nIT 101,Intro to Computing\n'),
    ('curriculum.xlsx', None), ('curriculum.docx', None), ('curriculum.pdf', PDF),
    ('photo.png', None), ('photo.jpg', None), ('PHOTO.JPEG', None),
])
def test_genuine_files_of_allowed_types_are_accepted(client, name, data):
    if data is None:
        data = {'.xlsx': _xlsx, '.docx': _docx, '.png': _png, '.jpg': _jpg, '.jpeg': _jpg}[name[name.rfind('.'):].lower()]()
    r = _post(client, data, name)
    assert r.status_code == 200 and r.data == b'VIEW REACHED', (name, r.get_data(as_text=True)[:200])


@pytest.mark.parametrize('name', ['shell.php', 'page.html', 'run.exe', 'image.svg', 'macro.xlsm', 'script.js',
                                  'noextension', 'archive.zip', 'report.pdf.exe'])
def test_disallowed_extensions_are_refused(client, name):
    r = _post(client, b'Course Code,Title\n', name)
    assert r.status_code == 400 and 'not an allowed file type' in r.get_json()['error'], name


@pytest.mark.parametrize('name,data,why', [
    ('curriculum.pdf', b'Course Code,Title\nIT 101,x\n', 'not a real PDF'),        # text renamed .pdf
    ('curriculum.pdf', EXE, 'not a real PDF'),
    ('curriculum.csv', EXE, 'not a plain-text CSV'),                               # program renamed .csv
    ('curriculum.csv', b'Course\x00Code\x00', 'not a plain-text CSV'),
    ('curriculum.csv', PDF, 'not a plain-text CSV'),
    ('photo.png', b'<svg onload=alert(1)>', 'not a real PNG'),
    ('photo.jpg', PDF, 'not a real JPG'),
    ('curriculum.xlsx', b'not a zip at all', 'not a valid file'),
    ('curriculum.xlsx', None, 'not a valid file'),                                  # a zip, but not Excel
    ('curriculum.docx', None, 'not a valid file'),
    ('curriculum.xls', b'Course Code\n', 'not a real Excel'),
])
def test_content_that_does_not_match_the_extension_is_refused(client, name, data, why):
    if data is None:
        data = _zip({'readme.txt': 'hello'})
    r = _post(client, data, name)
    assert r.status_code == 400 and why in r.get_json()['error'], (name, r.get_data(as_text=True)[:200])


def test_xlsx_and_docx_types_are_not_interchangeable(client):
    assert _post(client, _docx(), 'curriculum.xlsx').status_code == 400
    assert _post(client, _xlsx(), 'curriculum.docx').status_code == 400


def test_zip_bomb_is_refused(client, monkeypatch):
    monkeypatch.setattr(app_module, '_ZIP_MAX_UNCOMPRESSED', 10_000)
    bomb = _zip({'[Content_Types].xml': '<Types/>', 'xl/workbook.xml': '<w/>', 'xl/worksheets/sheet1.xml': 'A' * 50_000})
    assert len(bomb) < 2_000                    # tiny on the wire, 50 KB once opened
    r = _post(client, bomb, 'curriculum.xlsx')
    assert r.status_code == 400 and 'too large once opened' in r.get_json()['error']


def test_request_over_the_size_limit_gets_413(client, monkeypatch):
    monkeypatch.setitem(app_module.app.config, 'MAX_CONTENT_LENGTH', 50_000)
    r = _post(client, b'a,b\n' * 20_000, 'big.csv')
    assert r.status_code == 413
    assert r.get_json() == {'success': False, 'error': f'The file is too large. The maximum upload size is '
                                                      f'{app_module.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.'}


def test_size_limit_is_configured():
    assert app_module.app.config['MAX_CONTENT_LENGTH'] == app_module.MAX_UPLOAD_BYTES == 5 * 1024 * 1024


def test_real_size_upload_just_over_the_limit_is_refused_and_under_it_accepted(client):
    """The real 5 MB limit, not a lowered test value: 5 MB + 1 KB is refused, 4.9 MB passes."""
    over = b'a,b\n' * ((5 * 1024 * 1024 + 1024) // 4)
    r = _post(client, over, 'big.csv')
    assert r.status_code == 413 and '5 MB' in r.get_json()['error']
    under = b'a,b\n' * ((49 * 1024 * 1024 // 10) // 4)
    r = _post(client, under, 'ok.csv')
    assert r.status_code == 200 and r.data == b'VIEW REACHED'


def test_real_project_files_fit_under_the_limit():
    """The largest import files actually used (subject offerings, faculty lists) pass."""
    from pathlib import Path
    folder = Path(__file__).resolve().parents[2]          # NEW CAPSTONE
    files = [p for ext in ('*.xlsx', '*.pdf', '*.docx', '*.csv') for p in folder.rglob(ext)
             if '.git' not in p.parts]
    if not files:
        pytest.skip('no sample import files next to the project')
    assert max(p.stat().st_size for p in files) < app_module.MAX_UPLOAD_BYTES


def test_refused_page_upload_goes_back_with_a_message(client):
    r = client.post(URL, content_type='multipart/form-data',
                    headers={'Sec-Fetch-Mode': 'navigate', 'Referer': 'http://localhost/admin/curriculum'},
                    data={'file': (io.BytesIO(EXE), 'curriculum.csv')})
    assert r.status_code == 302 and r.headers['Location'].endswith('/admin/curriculum')
    with client.session_transaction() as s:
        assert any('not a plain-text CSV' in m for _, m in s.get('_flashes', []))


def test_every_file_field_is_checked_not_only_the_first(client):
    r = client.post(URL, headers=FETCH, content_type='multipart/form-data',
                    data={'file': (io.BytesIO(b'a,b\n'), 'ok.csv'), 'extra': (io.BytesIO(EXE), 'evil.csv')})
    assert r.status_code == 400


def test_refused_filename_is_sanitised_in_the_message(client):
    r = _post(client, b'x', '<img src=x onerror=1>.exe')
    assert '<img' not in r.get_json()['error']


def test_profile_photo_view_still_verifies_the_image_itself():
    """Second layer kept: the photo view re-checks with Pillow and a 2 MB cap."""
    import inspect
    src = inspect.getsource(app_module._write_profile_photo_file)
    assert 'img.verify()' in src and '_PROFILE_PHOTO_MAX_BYTES' in src
