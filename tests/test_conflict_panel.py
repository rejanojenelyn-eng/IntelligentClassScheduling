"""
Conflict panel data: the evaluation's single de-duplicated `conflicts` list
(_build_conflict_list) that drives the Generation page's conflict panel, its
count, the row indicators and Select Conflict Rows.

Each conflict names the rows of THIS schedule it targets and the lock(s) to
open by default (resolution_components) â€” the component(s) the solver should
change, not every value mentioned, so valid values stay locked.
"""
import copy

import pytest

import app as app_module
from conftest import requires_db

build = app_module._build_conflict_list

SCHEDULE = [
    {'subject_code': 'EDUC 018', 'faculty_id': '19007'},
    {'subject_code': 'ELED 311', 'faculty_id': '19007'},
    {'subject_code': 'ELED 314', 'faculty_id': '20094'},
    {'subject_code': 'IT 101', 'class_type': 'Lecture', 'faculty_id': 'A1'},
    {'subject_code': 'IT 101', 'class_type': 'Lab', 'faculty_id': 'B2'},
]


def _one(v, schedule=SCHEDULE, cross=None):
    [c] = build([v], cross or [], schedule)
    return c


def test_room_clash_opens_room_and_time_days():
    # resolution = the conflict's affected_components normalized to UI locks.
    c = _one({'rule': 'HC11', 'subject': 'ELED 314 / EDUC 018', 'detail': 'Room clash',
              'affected_components': ['room', 'day', 'time']})
    assert c['resolution_components'] == ['schedule', 'room']
    assert [t['subject_code'] for t in c['targets']] == ['ELED 314', 'EDUC 018']  # both rows


def test_faculty_clash_opens_instructor_and_time_days():
    c = _one({'rule': 'HC10', 'subject': 'EDUC 018 / ELED 311', 'detail': 'x',
              'affected_components': ['instructor', 'day', 'time']})
    assert c['resolution_components'] == ['faculty', 'schedule']
    # With no components on the violation, the per-rule fallback agrees.
    assert _one({'rule': 'HC10', 'subject': 'EDUC 018', 'detail': 'x'})['resolution_components'] ==         ['faculty', 'schedule']


@pytest.mark.parametrize('names', [['day'], ['time'], ['day', 'time'], ['days'], ['time_days'],
                                   ['day_time'], ['time_day'], ['Day', 'TIME']])
def test_any_day_or_time_component_maps_to_the_single_time_days_lock(names):
    c = _one({'rule': 'HCX', 'subject': 'ELED 314', 'detail': 'x', 'affected_components': names})
    assert c['resolution_components'] == ['schedule']


@pytest.mark.parametrize('rule,expected', [
    ('HC1', ['schedule']),   # availability: move within the instructor's hours, keep instructor
    ('HC5', ['schedule']), ('HC6', ['schedule']), ('HC7', ['schedule']), ('HC12', ['schedule']),
    ('HC9', ['faculty']), ('HC8', ['faculty', 'schedule']), ('HC17', ['faculty']),
    ('HC13', ['room']), ('HC14', ['room']),
])
def test_resolution_components_per_rule(rule, expected):
    assert _one({'rule': rule, 'subject': 'ELED 314', 'detail': 'x'})['resolution_components'] == expected


def test_unknown_rule_falls_back_to_affected_components_then_all_three():
    assert _one({'rule': 'HC16', 'subject': 'ELED 314', 'detail': 'x',
                 'affected_components': ['room']})['resolution_components'] == ['room']
    assert _one({'rule': 'HCX', 'subject': 'ELED 314', 'detail': 'x'})['resolution_components'] == \
        ['faculty', 'schedule', 'room']


def test_faculty_load_multiple_targets_every_row_of_that_faculty():
    c = _one({'rule': 'HC9', 'subject': 'multiple', 'faculty_id': '19007', 'detail': 'Teguenos overload'})
    assert c['targets'] == [{'subject_code': 'EDUC 018', 'faculty_id': '19007'},
                            {'subject_code': 'ELED 311', 'faculty_id': '19007'}]
    assert c['resolution_components'] == ['faculty']


def test_faculty_load_on_a_split_subject_targets_only_that_faculty_part():
    c = _one({'rule': 'HC9', 'subject': 'multiple', 'faculty_id': 'B2', 'detail': 'x'})
    assert c['targets'] == [{'subject_code': 'IT 101', 'faculty_id': 'B2'}]


def test_load_conflict_without_faculty_or_rows_is_listed_but_not_actionable():
    c = _one({'rule': 'HC9', 'subject': 'multiple', 'detail': 'legacy load message'})
    assert c['targets'] == []


def test_other_side_of_a_cross_section_conflict_is_not_a_target():
    v = {'rule': 'HC10', 'subject': 'EDUC 018 / ELED 105', 'detail': 'x',
         'affected_components': ['instructor', 'day', 'time']}
    c = _one(v, cross=[v])
    assert c['source'] == 'cross_section'
    assert c['targets'] == [{'subject_code': 'EDUC 018', 'faculty_id': None}]


# â”€â”€ Real-DB: the evaluation object â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.fixture(scope='module')
def beed3():
    from database import query_db
    sid = query_db("""SELECT s.sectionid FROM sections s
                      JOIN program_yearlevel p ON p.programyearlevelid = s.programyearlevelid
                      WHERE p.programcode = 'BEED' AND p.yearlevel = 3
                        AND p.academicyearid = 'AY2627' LIMIT 1""", one=True)['sectionid']
    body = app_module.app.test_client().post('/api/schedule/retrieve-previous', json={
        'program': 'BEED', 'yearLevel': 3, 'term': 'A', 'acadYear': 'AY2627',
        'section': str(sid)}).get_json()
    assert body['success'] is True
    return dict(body, section=str(sid))


@requires_db
def test_retrieved_schedule_conflicts_have_rows_and_components(beed3):
    ev = beed3['evaluation']
    conflicts = ev['conflicts']
    assert len(conflicts) == ev['hardViolationCount'] == 4     # one source, one count
    by_rule = {}
    for c in conflicts:
        by_rule.setdefault(c['rule'], []).append(c)
    assert by_rule['HC6'][0]['targets'] == [{'subject_code': 'ELED 320', 'faculty_id': None}]
    assert by_rule['HC6'][0]['resolution_components'] == ['schedule']
    # Cross-section clashes now carry components, and only this section's row is a target.
    assert {t['subject_code'] for c in by_rule['HC10'] for t in c['targets']} == {'EDUC 018'}
    assert all(c['affected_components'] == ['instructor', 'day', 'time'] for c in by_rule['HC10'])
    assert all(c['resolution_components'] == ['faculty', 'schedule'] for c in by_rule['HC10'])
    assert by_rule['HC11'][0]['affected_components'] == ['room', 'day', 'time']
    assert by_rule['HC11'][0]['resolution_components'] == ['schedule', 'room']
    # Retrieve Previous itself is unchanged: the conflicting rows are still displayed.
    assert {r['subject_code'] for r in beed3['schedule_data']} >= {'EDUC 018', 'ELED 317', 'ELED 314', 'ELED 320'}


@requires_db
def test_duplicate_conflicts_are_counted_once(beed3):
    v = {'rule': 'HC11', 'type': 'Conflict: Room Schedule', 'subject': 'ELED 314 / ELED 116',
         'detail': 'Room LQ117 is already occupied.', 'affected_components': ['room', 'day', 'time']}
    ev = app_module._compute_schedule_evaluation(
        copy.deepcopy(beed3['schedule_data']), 'BEED', 3, 'A',
        cross_violations=[v, dict(v)], acad_year='AY2627', section_id=beed3['section'])
    dupes = [c for c in ev['conflicts'] if c['detail'] == v['detail']]
    assert len(dupes) == 1
    assert ev['hardViolationCount'] == len(ev['conflicts'])


@requires_db
def test_conflict_list_is_rebuilt_from_the_changed_schedule(beed3):
    # Moving ELED 320 to a standard block removes its HC6 conflict from the list.
    rows = copy.deepcopy(beed3['schedule_data'])
    r = next(x for x in rows if x['subject_code'] == 'ELED 320')
    r.update(start_time='16:30', end_time='19:30', time='4:30 PM - 7:30 PM')
    ev = app_module._compute_schedule_evaluation(rows, 'BEED', 3, 'A', acad_year='AY2627',
                                                 section_id=beed3['section'])
    assert not any(c['rule'] == 'HC6' for c in ev['conflicts'])
    assert len(ev['conflicts']) == ev['hardViolationCount']

