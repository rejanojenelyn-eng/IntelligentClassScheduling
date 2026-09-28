"""
Fix #1 — Retrieve Previous resolves historical subject codes to the CURRENT
selected curriculum (app._resolve_current_curriculum_subjects).

Root cause of the false "not in the selected curriculum" warning: historical
data stores e.g. "ELEC BEED -GEE2" while the current curriculum has
"ELEC BEED-GEE2"; Retrieve Previous compared the raw historical code, while
regeneration emits the curriculum's own code — so the warning vanished only
after regenerating.
"""
import pytest

import app as app_module
from conftest import requires_db

# program BEED, curriculum 2022-2023; only the rows matching the queried
# (year level, semester) scope are returned by the fake query below.
_CURRICULUM = [
    {'curriculumsubjectid': 198, 'curriculumid': 9, 'subjectcode': 'ELEC BEED-GEE1',
     'subjectname': 'Elective 1', 'lecturehours': 3, 'laboratoryhours': 0, 'creditunits': 3,
     'yearlevel': 2, 'semester': 'A'},
    {'curriculumsubjectid': 199, 'curriculumid': 9, 'subjectcode': 'ELEC BEED-GEE2',
     'subjectname': 'Elective 2', 'lecturehours': 3, 'laboratoryhours': 0, 'creditunits': 3,
     'yearlevel': 2, 'semester': 'A'},
    {'curriculumsubjectid': 200, 'curriculumid': 9, 'subjectcode': 'ELED 102',
     'subjectname': 'Eled', 'lecturehours': 2, 'laboratoryhours': 1, 'creditunits': 3,
     'yearlevel': 2, 'semester': 'A'},
    {'curriculumsubjectid': 207, 'curriculumid': 9, 'subjectcode': 'ELEC BEED-GEE3',
     'subjectname': 'Elective 3', 'lecturehours': 3, 'laboratoryhours': 0, 'creditunits': 3,
     'yearlevel': 2, 'semester': 'B'},
    {'curriculumsubjectid': 300, 'curriculumid': 9, 'subjectcode': 'EDUC 301',
     'subjectname': 'Year 3 only', 'lecturehours': 3, 'laboratoryhours': 0, 'creditunits': 3,
     'yearlevel': 3, 'semester': 'A'},
]


def _fake_query_db(sql, params=None, one=False):
    _prog, _curr, year_level, term = params
    rows = [dict(r) for r in _CURRICULUM
            if r['yearlevel'] == year_level and r['semester'] == term]
    return rows


@pytest.fixture
def resolve(monkeypatch):
    monkeypatch.setattr(app_module, 'query_db', _fake_query_db)

    def _run(code, year_level=2, term='A'):
        row = {'subject_code': code, 'description': 'historical name',
               'lec_hours': 9, 'lab_hours': 9, 'faculty_id': 'F1',
               'start_time': '08:00', 'end_time': '09:30', 'days_list': ['Monday'], 'room_id': 5}
        app_module._resolve_current_curriculum_subjects(
            [row], 'BEED', year_level, term, '2022-2023')
        return row
    return _run


def _subject_refs(year_level=2, term='A'):
    return {'subjects': {app_module._norm_subject_code(r['subjectcode']) for r in _CURRICULUM
                         if r['yearlevel'] == year_level and r['semester'] == term}}


def _mismatch(row, year_level=2, term='A'):
    refs = dict(_subject_refs(year_level, term), faculty={'F1'}, room={'5'},
                timeslot={480, 570})
    comps, _reasons = app_module._unresolved_components(row, refs)
    return 'subject' in comps


def test_a1_exact_code_resolves_to_current_record(resolve):
    row = resolve('ELED 102')
    assert row['curriculumsubjectid'] == 200 and row['curriculumid'] == 9
    assert (row['lec_hours'], row['lab_hours'], row['credit_units']) == (2, 1, 3)
    assert row['description'] == 'Eled'           # current name, not historical
    assert not _mismatch(row)


@pytest.mark.parametrize('hist', ['ELEC BEED -GEE2', 'ELEC BEED - GEE2', 'ELEC BEED- GEE2'])
def test_a2_hyphen_spacing_is_the_same_subject(resolve, hist):
    row = resolve(hist)
    assert row['subject_code'] == 'ELEC BEED-GEE2'
    assert row['historical_subject_code'] == hist
    assert row['curriculumsubjectid'] == 199
    assert not _mismatch(row)


@pytest.mark.parametrize('hist', ['elec beed-gee1', '  ELEC BEED-GEE1  ', 'Elec  Beed-Gee1'])
def test_a3_case_and_outer_whitespace_are_the_same_subject(resolve, hist):
    row = resolve(hist)
    assert row['subject_code'] == 'ELEC BEED-GEE1' and row['curriculumsubjectid'] == 198
    assert not _mismatch(row)


def test_a4_subject_removed_from_current_curriculum_still_mismatches(resolve):
    row = resolve('ELEC BEED-GEE9')
    assert row['subject_resolution'] == 'not_in_curriculum'
    assert 'curriculumsubjectid' not in row
    assert _mismatch(row)


def test_a5_subject_only_in_another_year_level_is_not_matched(resolve):
    row = resolve('EDUC 301', year_level=2)
    assert 'curriculumsubjectid' not in row
    assert _mismatch(row)


def test_a6_subject_only_in_another_semester_is_not_matched(resolve):
    row = resolve('ELEC BEED -GEE3', term='A')   # GEE3 is a semester-B subject
    assert 'curriculumsubjectid' not in row
    assert _mismatch(row, term='A')


def test_ambiguous_canonical_match_is_not_guessed(monkeypatch):
    dup = [dict(_CURRICULUM[0]), dict(_CURRICULUM[0], curriculumsubjectid=999,
                                        subjectcode='ELECBEED-GEE1')]
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: [dict(r) for r in dup])
    row = {'subject_code': 'ELEC BEED -GEE1'}
    app_module._resolve_current_curriculum_subjects([row], 'BEED', 2, 'A', '2022-2023')
    assert row['subject_resolution'] == 'ambiguous'
    assert 'curriculumsubjectid' not in row and row['subject_code'] == 'ELEC BEED -GEE1'
    assert 'subject' in row['incomplete_components']


@requires_db
def test_a7_retrieved_and_generated_rows_share_current_identity():
    from database import query_db
    from scheduler import IntelligentScheduler
    sid = query_db("""SELECT s.sectionid FROM sections s
                      JOIN program_yearlevel p ON p.programyearlevelid = s.programyearlevelid
                      WHERE p.programcode = 'BEED' AND p.yearlevel = 2
                        AND p.academicyearid = 'AY2627' LIMIT 1""", one=True)
    if not sid:
        pytest.skip('no BEED 2 section in AY2627')
    body = app_module.app.test_client().post('/api/schedule/retrieve-previous', json={
        'program': 'BEED', 'yearLevel': 2, 'term': 'A', 'acadYear': 'AY2627',
        'section': str(sid['sectionid']), 'curriculum': '2022-2023'}).get_json()
    if not body.get('success'):
        pytest.skip('no previous AY history for BEED 2')
    rows = {r['subject_code']: r for r in body['schedule_data']}
    # Generation's own subject list for the same scope (fetch_data's query).
    subjects, *_ = IntelligentScheduler().fetch_data('BEED', 2, 'A', '2022-2023')
    generated_codes = {s['subjectcode'] for s in subjects}
    elec = [c for c in rows if c.startswith('ELEC BEED')]
    assert elec, 'expected the historical ELEC BEED -GEEx rows'
    for code in elec:
        assert code in generated_codes                        # same official code
        assert rows[code]['curriculumsubjectid']              # current record id
        assert 'subject' not in (rows[code].get('incomplete_components') or [])
