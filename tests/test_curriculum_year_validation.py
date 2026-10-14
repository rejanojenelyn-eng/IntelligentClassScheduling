"""
Curriculum year validation: YYYY-YYYY where the ending year is exactly the
starting year + 1, enforced on the server for manual entry, every import format
(CSV / Excel / PDF / Word) and Settings add/edit -- before anything is saved.
"""
import io
import json

import pytest

import app as app_module
from app import academic_year_problem, curriculum_file_year_problems, _full_curriculum_year
from conftest import requires_db


@pytest.mark.parametrize('year', ['2023-2024', '2024-2025', '2025-2026', '2026-2027',
                                  '1999-2000', '2035-2036', ' 2024-2025 '])
def test_consecutive_years_are_valid(year):
    assert academic_year_problem(year) is None


@pytest.mark.parametrize('year, reason', [
    ('2023-2025', 'spans 2 years'),
    ('2026-2029', 'spans 3 years'),
    ('2028-2020', 'must come after the starting year'),
    ('2025-2025', 'must come after the starting year'),
    ('2027-2026', 'must come after the starting year'),
    ('2021', 'YYYY-YYYY format'),
    ('2024/2025', 'YYYY-YYYY format'),
    ('24-25', 'YYYY-YYYY format'),
    ('', 'is required'),
])
def test_invalid_years_are_rejected_with_a_reason(year, reason):
    err = academic_year_problem(year)
    assert err and reason in err


def test_file_rows_with_invalid_years_are_identified():
    rows = [['CY2223', 'GEED 001'], ['CY2325', 'GEED 002'], ['', 'GEED 003'],
            ['2028-2020', 'GEED 004'], ['CY9900', 'GEED 005'], ['CY1819', 'GEED 006']]
    problems = curriculum_file_year_problems(rows, 0)
    assert len(problems) == 2
    assert problems[0].startswith('Row 3 (Curriculum Code "CY2325")')
    assert problems[1].startswith('Row 5 (Curriculum Code "2028-2020")')
    assert curriculum_file_year_problems(rows, None) == []     # no code column mapped


def test_placeholder_curricula_get_a_full_academic_year():
    assert _full_curriculum_year('2021') == '2021-2022'
    assert academic_year_problem(_full_curriculum_year(2025)) is None


# ── Routes: invalid years are blocked before anything is written ────────────────

@pytest.fixture
def admin():
    from database import query_db
    row = query_db("SELECT username FROM accounts WHERE role = 'Admin' AND isactive LIMIT 1", one=True)
    if not row:
        pytest.skip('no active Admin account')
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, username=row['username'], role='Admin',
                 must_change_password=False, account_setup_complete=True)
    return client


def _curriculum_count():
    from database import query_db
    return query_db("SELECT COUNT(*) AS n FROM curriculum", one=True)['n']


def _program():
    from database import query_db
    return query_db("SELECT programcode FROM programs ORDER BY programcode LIMIT 1", one=True)['programcode']


def _flashes(client):
    with client.session_transaction() as s:
        return [m for _, m in s.get('_flashes', [])]


CSV = b'cc,sc,sn,u\nCY2324,GEED 001,Understanding the Self,3\n'
MAP = {'col_0': 'cc', 'col_1': 'sc', 'col_2': 'sn', 'col_3': 'u'}


@requires_db
@pytest.mark.parametrize('year', ['2028-2020', '2023-2025', '2025-2025'])
def test_csv_import_rejects_invalid_year_without_saving(admin, year):
    before = _curriculum_count()
    r = admin.post('/admin/curriculum/import', headers={'Referer': '/admin/curriculum'},
                   data=dict(MAP, file=(io.BytesIO(CSV), 'c.csv'), program_code=_program(), curriculum_year=year))
    assert r.status_code == 302
    assert any('Import Blocked' in m and year in m for m in _flashes(admin))
    assert _curriculum_count() == before


@requires_db
def test_csv_import_names_the_bad_row_in_the_file(admin):
    csv_bad = b'cc,sc,sn,u\nCY2324,GEED 001,Self,3\nCY2325,GEED 002,Readings,3\n'
    before = _curriculum_count()
    admin.post('/admin/curriculum/import', headers={'Referer': '/admin/curriculum'},
               data=dict(MAP, file=(io.BytesIO(csv_bad), 'c.csv'), program_code=_program(),
                         curriculum_year='2023-2024'))
    assert any('Row 3 (Curriculum Code "CY2325")' in m for m in _flashes(admin))
    assert _curriculum_count() == before


@requires_db
def test_xlsx_import_rejects_invalid_year_without_saving(admin):
    openpyxl = pytest.importorskip('openpyxl')
    wb = openpyxl.Workbook()
    wb.active.append(['cc', 'sc', 'sn', 'u'])
    wb.active.append(['CY2324', 'GEED 001', 'Self', 3])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    before = _curriculum_count()
    admin.post('/admin/curriculum/import/xlsx', headers={'Referer': '/admin/curriculum'},
               data=dict(MAP, file=(buf, 'c.xlsx'), program_code=_program(), curriculum_year='2026-2029'))
    assert any('spans 3 years' in m for m in _flashes(admin))
    assert _curriculum_count() == before


@requires_db
def test_analyze_endpoints_reject_invalid_year(admin):
    r = admin.post('/admin/curriculum/import/csv/analyze',
                   data=dict(MAP, file=(io.BytesIO(CSV), 'c.csv'), curriculum_year='2027-2026'))
    assert r.status_code == 400 and 'must come after the starting year' in r.get_json()['error']
    r = admin.post('/admin/curriculum/import/csv/analyze',
                   data=dict(MAP, file=(io.BytesIO(CSV), 'c.csv'), curriculum_year='2023-2024'))
    assert r.status_code == 200 and r.get_json()['subject_count'] == 1      # valid year still works
    for path, name in (('/admin/curriculum/import/pdf/analyze', 'c.pdf'),
                       ('/admin/curriculum/import/docx/analyze', 'c.docx')):
        r = admin.post(path, data={'file': (io.BytesIO(b'x'), name), 'curriculum_year': '2023-2025'})
        assert r.status_code == 400 and 'spans 2 years' in r.get_json()['error']


@requires_db
def test_pdf_confirm_rejects_invalid_year_without_saving(admin):
    before = _curriculum_count()
    admin.post('/admin/curriculum/import/pdf/confirm', data={
        'program_code': _program(), 'curriculum_year': '2028-2020',
        'subjects_data': json.dumps([{'sc': 'GEED 001', 'sn': 'Self', 'yl': 1, 'sem': 'A', 'u': 3}])})
    assert any('Import Blocked' in m and '2028-2020' in m for m in _flashes(admin))
    assert _curriculum_count() == before


@requires_db
def test_settings_add_and_edit_reject_invalid_year(admin):
    from database import query_db
    before = _curriculum_count()
    admin.post('/admin/settings/curriculum/add',
               data={'program_code': _program(), 'curriculum_year': '2023-2025'})
    assert any('spans 2 years' in m for m in _flashes(admin))
    assert _curriculum_count() == before
    cur = query_db("SELECT curriculumid, curriculumyear, curriculumcode FROM curriculum LIMIT 1", one=True)
    admin.post('/admin/settings/curriculum/edit', data={
        'curriculum_id': cur['curriculumid'], 'curriculum_year': '2027-2026',
        'curriculum_code': cur['curriculumcode']})
    assert any('must come after the starting year' in m for m in _flashes(admin))
    after = query_db("SELECT curriculumyear FROM curriculum WHERE curriculumid = %s",
                     (cur['curriculumid'],), one=True)
    assert after['curriculumyear'] == cur['curriculumyear']
