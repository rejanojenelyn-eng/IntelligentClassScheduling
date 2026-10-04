"""
Faculty → Class Schedule → EXPORT SCHEDULE uses the Academic Head's Class
Schedule export dialog (templates/_schedule_export_modal.html +
static/js/schedule_export_modal.js), with Academic Year / Semester fixed to the
active term. The server always uses the active term and Published schedules,
whatever Academic Year / Semester the request carries.
"""
import csv
import io
import zipfile

import pytest

from conftest import requires_db, DB_AVAILABLE

pytestmark = requires_db

if DB_AVAILABLE:
    import app as app_module
    from database import query_db

FAC = '12079'
COUNT, EXPORT = '/faculty/schedule/export/count', '/faculty/schedule/export'


def _client(username=FAC, role='Faculty'):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=username, role=role,
                 must_change_password=False, account_setup_complete=True)
    return c


@pytest.fixture
def term():
    t = query_db("""SELECT s.academicyearid, s.semestertype, a.yearstart, a.yearend
                    FROM semester s JOIN academicyear a ON a.academicyearid = s.academicyearid
                    WHERE s.isactive LIMIT 1""", one=True)
    if not t or not query_db("SELECT 1 FROM accounts WHERE username = %s AND isactive", [FAC]):
        pytest.skip('needs an active term and faculty account 12079')
    return t


def _published_rows(term, programs=(), year_levels=()):
    conn = app_module.get_db_connection()
    cur = conn.cursor(cursor_factory=app_module.RealDictCursor)
    try:
        return app_module._sch_exp_fetch(cur, [term['academicyearid']], [term['semestertype']],
                                         list(programs), list(year_levels), published_only=True)
    finally:
        cur.close(); conn.close()


def _a_program(term):
    rows = _published_rows(term)
    if not rows:
        pytest.skip('no published schedule in the active term')
    return rows[0]['Program']


# ── the page ────────────────────────────────────────────────────────────────

def test_faculty_page_renders_the_academic_head_dialog_with_the_active_term_fixed(term):
    html = _client().get('/faculty_schedule').get_data(as_text=True)
    assert 'id="seModal"' in html and 'id="seLayoutModal"' in html
    assert 'onclick="openSchedExport()"' in html and 'fcsExportModal' not in html
    assert 'js/schedule_export_modal.js' in html and 'css/schedule_export_modal.css' in html
    assert "countUrl: '/faculty/schedule/export/count'" in html
    # exactly one AY and one semester option, both the active term, checked and not toggleable
    assert html.count('class="se-ay-cb se-fixed-cb"') == 1 and html.count('class="se-sem-cb') == 1
    assert f'value="{term["academicyearid"]}" checked tabindex="-1" onclick="return false"' in html
    assert f'AY {term["yearstart"]}-{term["yearend"]}' in html
    assert 'class="se-prog-cb"' in html and html.count('class="se-yl-cb"') == 5


def test_academic_head_page_still_has_every_term_selectable():
    html = _client('94045', 'Academic Head').get('/schedule').get_data(as_text=True) \
        if query_db("SELECT 1 FROM accounts WHERE username='94045' AND isactive") else ''
    if not html:
        pytest.skip('Academic Head account 94045 not present')
    assert 'id="seModal"' in html and 'se-fixed' not in html.split('id="seModal"')[1].split('</div>\n</div>')[0]
    assert html.count('class="se-sem-cb"') == 3
    n_ays = query_db("SELECT COUNT(*) AS n FROM academicyear", one=True)['n']
    assert html.count('class="se-ay-cb"') == n_ays
    assert 'SE_EXPORT_CONFIG' not in html          # uses the default /academic/... endpoints


# ── count + export ──────────────────────────────────────────────────────────

def test_count_is_the_active_term_published_rows(term):
    c = _client()
    got = c.post(COUNT, json={}).get_json()
    rows = _published_rows(term)
    assert got['count'] == len(rows) and got['programs'] == len({r['Program'] for r in rows})
    prog = _a_program(term)
    got = c.post(COUNT, json={'programs': [prog]}).get_json()
    assert got['count'] == len(_published_rows(term, [prog]))


def test_academic_year_and_semester_in_the_request_are_ignored(term):
    c = _client()
    other = query_db("SELECT academicyearid FROM academicyear WHERE academicyearid <> %s LIMIT 1",
                     [term['academicyearid']], one=True)
    base = c.post(COUNT, json={}).get_json()
    forged = c.post(COUNT, json={'ay_ids': [other['academicyearid']] if other else ['0'],
                                 'sem_types': ['C']}).get_json()
    assert forged == base
    csv_a = c.post(EXPORT, json={'formats': ['csv']}).data
    csv_b = c.post(EXPORT, json={'formats': ['csv'], 'ay_ids': ['0'], 'sem_types': ['C']}).data
    assert csv_a == csv_b


def test_same_file_as_the_academic_head_export_for_the_same_published_rows(term):
    prog = _a_program(term)
    ours = _client().post(EXPORT, json={'formats': ['csv'], 'programs': [prog]}).data
    rows = _published_rows(term, [prog])
    assert ours == app_module._sch_gen_csv(rows)
    body = list(csv.reader(io.StringIO(ours.decode('utf-8-sig'))))
    assert {r[6] for r in body[1:]} == {prog}


@pytest.mark.parametrize('layout', ['table', 'calendar'])
def test_several_formats_download_as_one_zip(term, layout):
    prog = _a_program(term)
    r = _client().post(EXPORT, json={'formats': ['pdf', 'docx', 'xlsx', 'csv'], 'programs': [prog],
                                     'layout': layout, 'filename': 'schedule_BSIT'})
    assert r.status_code == 200 and r.mimetype == 'application/zip'
    assert 'filename="schedule_BSIT.zip"' in r.headers['Content-Disposition']
    with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
        assert sorted(zf.namelist()) == ['schedule_BSIT.csv', 'schedule_BSIT.docx',
                                         'schedule_BSIT.pdf', 'schedule_BSIT.xlsx']


def test_single_format_is_a_plain_file(term):
    r = _client().post(EXPORT, json={'formats': ['pdf'], 'programs': [_a_program(term)]})
    assert r.status_code == 200 and r.mimetype == 'application/pdf'


def test_bad_requests_and_access(term):
    c = _client()
    assert c.post(EXPORT, json={'formats': []}).status_code == 400
    assert c.post(EXPORT, json={'formats': ['txt']}).status_code == 400
    assert c.post(EXPORT, json={'formats': ['csv'], 'programs': ['NO-SUCH-PROGRAM']}).status_code == 404
    assert c.post(COUNT, json={'year_levels': ['x']}).status_code == 400
    anon = app_module.app.test_client()
    assert anon.post(COUNT, json={}).status_code == 403 and anon.post(EXPORT, json={'formats': ['csv']}).status_code == 403
    head = _client('94045', 'Academic Head')
    assert head.post(COUNT, json={}).status_code == 403
