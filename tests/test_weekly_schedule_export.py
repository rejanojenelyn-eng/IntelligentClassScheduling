"""
Faculty side → Weekly Schedule → Export (PDF / XLSX / CSV / DOCX, one file or a
.zip) and the per-week data behind the week navigation.

Checks the export uses the Teaching Assignment letterhead (logo, title, LOPEZ,
QUEZON CAMPUS, EMP NO / EMP STATUS info block), lists exactly the classes of
the requested week on that week's dates, blanks days outside the semester,
includes that week's approved make-ups, and is limited to the logged-in faculty.
"""
import csv
import io
from datetime import date, timedelta

import pytest

import weekly_schedule_export as ws
from conftest import DB_AVAILABLE

if DB_AVAILABLE:
    import app as app_module
    from database import query_db

FAC = '12079'
requires = pytest.mark.skipif(not DB_AVAILABLE, reason='local ASDBv11 Postgres database is not reachable')


# ── pure helpers (no DB) ────────────────────────────────────────────────────

def _sample(in_sem=None, makeup=False):
    week = [date(2026, 9, 28) + timedelta(days=i) for i in range(7)]
    e = [{'days': 'Monday', 'date': week[0], 'time_range': '10:00 AM - 12:00 PM', 'hrs': 2,
          'subjectcode': 'COMP 002', 'subjectname': 'Computer Programming 1', 'units': 3,
          'year_section': 'BSIT 1', 'room': 'LQ103', 'kind': 'class'},
         {'days': 'Monday', 'date': week[0], 'time_range': '11:00 AM - 12:30 PM', 'hrs': 1.5,
          'subjectcode': 'COMP 017', 'subjectname': 'Multimedia', 'units': 3,
          'year_section': 'BSIT3', 'room': 'LQ100', 'kind': 'class'}]
    if makeup:
        e.append({'days': 'Friday', 'date': week[4], 'time_range': '01:00 PM - 03:00 PM', 'hrs': 2,
                  'subjectcode': 'COMP 002', 'subjectname': 'Computer Programming 1', 'units': 3,
                  'year_section': 'DIT1', 'room': 'LQ104', 'kind': 'makeup'})
    return {'source': 'official', 'fullname': 'DOE, Jane', 'employeenumber': 'E1',
            'employee_status': 'Part-Time', 'dept_code': 'IT', 'sem_label': '1st Semester',
            'ay_label': '2026-2027', 'program_filter': '', 'week': week,
            'week_in_semester': in_sem or [True] * 7, 'sem_start': date(2026, 8, 1),
            'sem_end': date(2026, 12, 20), 'entries': e}


@pytest.mark.parametrize('fmt', ['xlsx', 'csv', 'pdf', 'docx'])
def test_every_format_builds(fmt):
    out = ws.GENERATORS[fmt](_sample(makeup=True), None)
    assert isinstance(out, bytes) and len(out) > 200


def test_title_uses_ta_letterhead_lines():
    lines = ws._title_lines(_sample())
    assert lines[:3] == ['WEEKLY SCHEDULE', 'FIRST SEMESTER, SY 2026-2027', 'WEEK OF SEP 28 – OCT 4, 2026']
    assert 'LOCAL SCHEDULE' in ws._title_lines(_sample() | {'source': 'local'})


def test_grid_blocks_span_rows_and_overlaps_share_one_block():
    blocks = ws.grid_model(_sample())
    assert len(blocks[0]) == 1                      # 10–12 and 11–12:30 overlap → one block
    r0, r1, text, _ = blocks[0][0]
    assert (r0, r1) == (5, 10)                      # 10:00 AM row … 12:30 PM (exclusive)
    assert 'COMP 002' in text and 'COMP 017' in text


def test_csv_list_hours_and_makeup_remark():
    rows = list(csv.reader(io.StringIO(ws.gen_csv(_sample(makeup=True)).decode('utf-8-sig'))))
    assert ['DAY', 'DATE', 'TIME'] == rows[rows.index(ws.LIST_HEADERS)][:3]
    fri = next(r for r in rows if r and r[0] == 'Friday')
    assert fri[1] == '10/2/2026' and fri[-1] == 'Make-up class'
    hours = next(r for r in rows if r and r[0] == 'HOURS')
    assert hours[1] == '3.5' and hours[5] == '2' and hours[-1] == '5.5'


def test_out_of_semester_days_are_marked():
    d = _sample(in_sem=[True] * 5 + [False, False])
    hours = next(r for r in csv.reader(io.StringIO(ws.gen_csv(d).decode('utf-8-sig'))) if r and r[0] == 'HOURS')
    assert hours[6:8] == ['—', '—']
    assert any('outside' in n for n in ws.notes(d))


# ── through the real route (DB) ─────────────────────────────────────────────

def _client(username=FAC, role='Faculty'):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s.update(loggedin=True, username=username, role=role,
                 must_change_password=False, account_setup_complete=True)
    return c


@pytest.fixture
def fac():
    if not query_db("SELECT 1 FROM accounts WHERE username = %s AND isactive", [FAC]):
        pytest.skip('faculty account 12079 not present')
    return _client()


def _sem():
    return query_db("""SELECT s.semstartdate, s.semenddate FROM semester s
                       JOIN academicyear a ON a.academicyearid = s.academicyearid
                       WHERE a.isactive ORDER BY s.semstartdate LIMIT 1""", one=True)


def _monday_inside_semester():
    s = _sem()
    start = s['semstartdate'] + timedelta(days=14)
    return start - timedelta(days=start.weekday())


def _export(c, fmts, **extra):
    return c.post('/faculty/weekly-schedule/export', json={'formats': fmts, **extra})


@requires
@pytest.mark.parametrize('fmt,mime', [
    ('xlsx', 'spreadsheetml'), ('csv', 'text/csv'), ('pdf', 'application/pdf'), ('docx', 'wordprocessingml')])
def test_each_format_downloads_the_requested_week(fac, fmt, mime):
    mon = _monday_inside_semester()
    r = _export(fac, [fmt], week_start=mon.isoformat())
    assert r.status_code == 200, r.get_json()
    assert mime in r.mimetype
    assert f'_{mon.isoformat()}_to_{(mon + timedelta(days=6)).isoformat()}.{fmt}"' in r.headers['Content-Disposition']


@requires
def test_csv_matches_the_published_load_for_that_week(fac):
    mon = _monday_inside_semester()
    text = _export(fac, ['csv'], week_start=mon.isoformat()).data.decode('utf-8-sig')
    for must in ('WEEKLY SCHEDULE', 'LOPEZ, QUEZON CAMPUS', 'EMP NO', 'EMP STATUS', 'CLASS LIST',
                 'TEACHING HOURS PER DAY', mon.strftime('%b').upper()):
        assert must in text, must
    load = fac.get('/api/faculty/my_teaching_load?source=official').get_json()
    for row in load['regular'] + load['partTime']:
        assert row['subjectcode'] in text and row['year_section'] in text


@requires
def test_any_day_of_the_week_maps_to_its_monday(fac):
    mon = _monday_inside_semester()
    r = _export(fac, ['csv'], week_start=(mon + timedelta(days=3)).isoformat())
    assert f'_{mon.isoformat()}_to_' in r.headers['Content-Disposition']


@requires
def test_week_outside_the_semester_has_no_classes(fac):
    before = _sem()['semstartdate'] - timedelta(days=60)
    text = _export(fac, ['csv'], week_start=before.isoformat()).data.decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[rows.index(ws.LIST_HEADERS) + 1] == []        # no class rows
    assert 'outside the' in text


@requires
def test_several_formats_zip(fac):
    import zipfile
    r = _export(fac, ['pdf', 'xlsx', 'csv', 'docx'], week_start=_monday_inside_semester().isoformat())
    assert r.status_code == 200 and r.mimetype == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
        assert sorted(n.rsplit('.', 1)[1] for n in zf.namelist()) == ['csv', 'docx', 'pdf', 'xlsx']


@requires
def test_program_filter_and_local_source(fac):
    mon = _monday_inside_semester().isoformat()
    text = _export(fac, ['csv'], week_start=mon, program='NOPE').data.decode('utf-8-sig')
    rows = list(csv.reader(io.StringIO(text)))
    assert 'PROGRAM: NOPE' in text and rows[rows.index(ws.LIST_HEADERS) + 1] == []
    assert 'LOCAL SCHEDULE' in _export(fac, ['csv'], week_start=mon, source='local').data.decode('utf-8-sig')


@requires
def test_invalid_formats_and_access(fac):
    for bad in (['txt'], [], ['pdf', 'exe']):
        assert _export(fac, bad).status_code == 400
    assert app_module.app.test_client().post('/faculty/weekly-schedule/export', json={'formats': ['pdf']}).status_code == 403
    assert _export(_client('admin', 'Admin'), ['pdf']).status_code == 403
    other = query_db("SELECT employeenumber FROM faculty WHERE employeenumber <> %s LIMIT 1", [FAC], one=True)
    r = _export(fac, ['csv'], emp_num=other['employeenumber'], employeenumber=other['employeenumber'])
    assert FAC in r.data.decode('utf-8-sig')


@requires
def test_my_makeups_endpoint_is_own_and_dated(fac):
    rows = query_db("""SELECT exception_date FROM schedule_exception_log
                       WHERE source_type = 'makeup_class' AND employeenumber = %s LIMIT 1""", [FAC], one=True)
    if not rows:
        pytest.skip('no approved make-up for 12079')
    d = rows['exception_date']
    got = fac.get(f'/api/faculty/my_makeups?start={d - timedelta(days=d.weekday())}'
                  f'&end={d + timedelta(days=6 - d.weekday())}').get_json()
    assert any(m['exception_date'] == d.isoformat() for m in got)
    assert app_module.app.test_client().get('/api/faculty/my_makeups').status_code == 401
