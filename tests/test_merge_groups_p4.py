"""HC16/HC17 group model — P4 generation integration (no database).

The Merge Group MEETING is the generation anchor (never a section): the plan,
the `merge_group` pins and how they survive CBR release / repair / lock
re-application / selective regeneration, user-lock conflicts, SAME/MULTIPLE
faculty, same-event occupancy, unscheduled/incomplete groups, Lecture/Lab and
multi-meeting groups, retrieved rows, and the partial-meeting HC17 correction.
"""
from datetime import time

import pytest

import faculty_load as fl
import merge_groups as mg
from scheduler import IntelligentScheduler, CSPValidator

GYM, QUAD, LAB = 10, 11, 12
ROOMS = {GYM: {'roomid': GYM, 'roomname': 'PUP GYM', 'roomtype': 'Lecture'},
         QUAD: {'roomid': QUAD, 'roomname': 'LQ-QUAD', 'roomtype': 'Lecture'},
         LAB: {'roomid': LAB, 'roomname': 'LAB 1', 'roomtype': 'Laboratory'}}


def meeting(mid, day='Sunday', start='09:00', end='12:00', room=GYM, ctype='Lecture'):
    return {'mergegroupmeetingid': mid, 'class_type': ctype, 'daydesc': day, 'start': start, 'end': end,
            'roomid': room, 'roomname': ROOMS.get(room, {}).get('roomname'),
            'roomtype': ROOMS.get(room, {}).get('roomtype'), 'starttimeid': None, 'endtimeid': None}


def group(gid, sections, *, code='NSTP 001', meetings=None, mode='MULTIPLE_FACULTY', designated=None,
          complete=('Lecture',), required=None, teaching=3.0, units=3.0, active=True):
    meetings = [meeting(gid * 10 + 1)] if meetings is None else meetings
    required = required or {'Lecture': 3.0}
    return {'mergegroupid': gid, 'groupname': f'Group {gid}', 'is_active': active, 'config_errors': [],
            'faculty_mode': mode, 'employeenumber': designated,
            'members': [{'sectionid': s, 'subjectcode': code, 'label': f'SEC-{s}'} for s in sections],
            'meetings': meetings, 'completeness': {'complete_types': list(complete), 'required': required},
            'state': 'SCHEDULED' if set(required) <= set(complete) and meetings else 'INCOMPLETE',
            'ref_teachinghours': teaching, 'ref_creditunits': units}


def policy(*groups, section=None, rules=None):
    return mg.GroupMergePolicy(mg.MergeIndex(list(groups)), default_section_id=section, load_rules=rules or {})


def live(section, *, fac='F1', code='NSTP 001', day='Sunday', start='09:00', end='12:00', room=GYM):
    return {'sectionid': section, 'subjectcode': code, 'day': day, 'start_time': start, 'end_time': end,
            'roomid': room, 'faculty_id': fac, 'programcode': 'BSIT', 'sectionname': f'S{section}',
            'yearlevel': 1, 'roomname': 'PUP GYM', 'origin': 'Official'}


def plan_for(pol, section, live_rows=()):
    return mg.generation_plan(pol, section, list(live_rows))


SUBJ = {'subjectcode': 'NSTP 001', 'subjectname': 'NSTP 1', 'offeringcode': 'BSIT',
        'lecturehours': 3, 'laboratoryhours': 0, 'creditunits': 3}


def gene(section=None, *, code='NSTP 001', ctype='Lecture', day='Monday', start=(7, 30), end=(10, 30),
         room=QUAD, fac='F9'):
    g = {'subject_code': code, 'class_type': ctype, 'course': 'BSIT', 'faculty_id': fac, 'instructor': fac,
         'room_id': room, 'room': 'X', 'room_type': 'Lecture', 'start_time': time(*start), 'end_time': time(*end),
         'days_list': [day], 'day': day, 'time': '', 'days': '', 'lec_hours': 3, 'lab_hours': 0,
         'duration_hrs': 3.0}
    if section is not None:
        g['section_id'] = section
    return g


# ── 1-4. Same anchor for every member, whatever the order ───────────────────────

@pytest.mark.parametrize('sections', [[1, 2], [1, 2, 3], [1, 2, 3, 4, 5]])
def test_every_member_gets_the_same_group_slot(sections):
    pol = policy(group(1, sections))
    slots = []
    for s in sections:
        e = plan_for(pol, s)['entries'][('NSTP 001', 'Lecture')]
        assert e['state'] == 'pinned'
        slots.append([(x['days'], x['start'], x['end'], x['roomid']) for x in e['sets']])
    assert all(s == [(['Sunday'], '09:00', '12:00', GYM)] for s in slots)


def test_plan_is_independent_of_generation_order():
    pol = policy(group(1, [1, 2, 3], mode='SAME_FACULTY'))
    published_first = [live(2, fac='X')]
    a = plan_for(pol, 1, published_first)['entries'][('NSTP 001', 'Lecture')]
    c = plan_for(pol, 3, published_first)['entries'][('NSTP 001', 'Lecture')]
    assert a['sets'] == c['sets'] and a['faculty'] == c['faculty'] == {'status': 'known', 'faculty_id': 'X',
                                                                         'known': ['X']}


# ── 5-6. Not (fully) merged groups are generated normally (P7) ─────────────────

def test_group_without_meetings_is_generated_like_any_subject():
    # P7: sections only ALLOWED to merge (no merged slot yet) are not pinned or blanked.
    assert plan_for(policy(group(1, [1, 2], meetings=[], complete=())), 1)['entries'] == {}


def test_partially_merged_class_type_is_generated_normally():
    half = [meeting(11, start='09:00', end='10:30')]
    assert plan_for(policy(group(1, [1, 2], meetings=half, complete=())), 1)['entries'] == {}


# ── 7-10. Lecture / Lab / multiple meetings ─────────────────────────────────────

def test_lecture_and_lab_meetings_pin_their_own_class_type():
    mts = [meeting(11, day='Monday', room=GYM, ctype='Lecture'), meeting(12, day='Tuesday', room=LAB, ctype='Lab')]
    g = group(1, [1, 2], code='CHEM 1', meetings=mts, complete=('Lecture', 'Lab'),
              required={'Lecture': 3.0, 'Lab': 3.0})
    entries = plan_for(policy(g), 1)['entries']
    assert entries[('CHEM 1', 'Lecture')]['sets'][0]['roomid'] == GYM
    assert entries[('CHEM 1', 'Lab')]['sets'][0]['roomid'] == LAB
    assert entries[('CHEM 1', 'Lab')]['sets'][0]['roomtype'] == 'Laboratory'


def test_lab_without_a_complete_lab_meeting_is_generated_normally_while_lecture_is_pinned():
    g = group(1, [1, 2], code='CHEM 1', meetings=[meeting(11, day='Monday')], complete=('Lecture',),
              required={'Lecture': 3.0, 'Lab': 3.0})
    entries = plan_for(policy(g), 1)['entries']
    assert entries[('CHEM 1', 'Lecture')]['state'] == 'pinned'
    assert ('CHEM 1', 'Lab') not in entries


def test_meetings_sharing_time_and_room_form_one_set_others_their_own():
    mon_thu = [meeting(11, day='Thursday', start='09:00', end='10:30'), meeting(12, day='Monday', start='09:00', end='10:30')]
    e = plan_for(policy(group(1, [1, 2], meetings=mon_thu)), 1)['entries'][('NSTP 001', 'Lecture')]
    assert [s['days'] for s in e['sets']] == [['Monday', 'Thursday']]
    split = [meeting(11, day='Monday', start='09:00', end='10:30'),
             meeting(12, day='Thursday', start='13:30', end='15:00', room=QUAD)]
    e = plan_for(policy(group(1, [1, 2], meetings=split)), 1)['entries'][('NSTP 001', 'Lecture')]
    assert [(s['days'], s['roomid']) for s in e['sets']] == [(['Monday'], GYM), (['Thursday'], QUAD)]


# ── 11-18. Pins: group values, survive everything, user locks win but are reported ──

def _pin(plan, user=None, cbr=None):
    s = IntelligentScheduler()
    locked = dict(cbr or {})
    locked.update(user or {})
    return s, s._apply_group_pins(locked, user or {}, [SUBJ], plan, {'D1': {'fullname': 'Dee One'}}, ROOMS)


KEY = ('NSTP 001', 'Lecture', 'BSIT')


def test_pin_sets_group_day_time_and_room_and_replaces_cbr():
    cbr = {KEY: {'source_case': 7, 'lock': {'schedule': True, 'room': True, 'faculty': True},
                 'days_list': ['Monday'], 'start_time': time(7, 30), 'end_time': time(10, 30), 'room_id': QUAD,
                 'faculty_id': 'H1'}}
    _s, (locked, reports) = _pin(plan_for(policy(group(1, [1, 2])), 1), cbr=cbr)
    pin = locked[KEY]
    assert pin['source'] == 'merge_group' and 'source_case' not in pin
    assert (pin['days_list'], pin['start_time'], pin['end_time'], pin['room_id']) == \
        (['Sunday'], time(9, 0), time(12, 0), GYM)
    assert pin['lock'] == {'schedule': True, 'room': True, 'faculty': False} and reports == []


def test_pin_survives_cbr_release_lock_reapplication_and_repair():
    s, (locked, _r) = _pin(plan_for(policy(group(1, [1, 2])), 1))
    assert s._release_cbr_conflicts([{'rule': 'HC11', 'subject': 'NSTP 001', 'detail': 'x'}], locked, []) is False
    g = gene()
    s._reapply_locks([g], locked)
    assert (g['day'], g['start_time'], g['room_id']) == ('Sunday', time(9, 0), GYM)
    other = gene(code='GEED 001', day='Sunday', start=(9, 0), end=(12, 0), room=GYM)
    ind = [dict(g), other]
    s._hc_cfg = {}
    s._nstp_force_sunday = False
    s._repair_overlaps(ind, {}, locked_parts=locked)
    assert (ind[0]['day'], ind[0]['start_time'], ind[0]['room_id']) == ('Sunday', time(9, 0), GYM)
    s._enforce_user_locks(ind, locked)
    assert (ind[0]['day'], ind[0]['start_time'], ind[0]['room_id']) == ('Sunday', time(9, 0), GYM)


def test_compatible_user_lock_is_kept_without_report():
    user = {KEY: {'lock': {'schedule': True, 'room': False, 'faculty': False}, 'days_list': ['Sunday'],
                  'day': 'Sunday', 'start_time': time(9, 0), 'end_time': time(12, 0)}}
    _s, (locked, reports) = _pin(plan_for(policy(group(1, [1, 2])), 1), user=user)
    assert reports == [] and locked[KEY]['room_id'] == GYM and locked[KEY]['user_lock']['schedule'] is True


@pytest.mark.parametrize('field,user', [
    ('schedule', {'lock': {'schedule': True}, 'days_list': ['Monday'], 'day': 'Monday',
                  'start_time': time(7, 30), 'end_time': time(10, 30)}),
    ('room', {'lock': {'room': True}, 'room_id': QUAD, 'room': 'LQ-QUAD'}),
])
def test_incompatible_user_lock_is_kept_and_reported(field, user):
    _s, (locked, reports) = _pin(plan_for(policy(group(1, [1, 2])), 1), user={KEY: user})
    assert [r['code'] for r in reports] == ['HC16_DIVERGED'] and reports[0]['group'] == 'Group 1'
    if field == 'schedule':
        assert locked[KEY]['days_list'] == ['Monday'] and locked[KEY]['room_id'] == GYM
    else:
        assert locked[KEY]['room_id'] == QUAD and locked[KEY]['days_list'] == ['Sunday']


def test_selective_regeneration_repins_selected_and_keeps_unselected():
    # selected row: only faculty preserved -> schedule/room re-pin to the group
    selected = {KEY: {'lock': {'faculty': True}, 'faculty_id': 'F5', 'instructor': 'F5'}}
    _s, (locked, _r) = _pin(plan_for(policy(group(1, [1, 2])), 1), user=selected)
    assert (locked[KEY]['days_list'], locked[KEY]['room_id'], locked[KEY]['faculty_id']) == (['Sunday'], GYM, 'F5')
    # unselected row (fully locked) keeps its own data
    unselected = {KEY: {'lock': {'faculty': True, 'room': True, 'schedule': True}, 'faculty_id': 'F5',
                        'room_id': GYM, 'days_list': ['Sunday'], 'day': 'Sunday',
                        'start_time': time(9, 0), 'end_time': time(12, 0)}}
    _s, (locked, reports) = _pin(plan_for(policy(group(1, [1, 2])), 1), user=unselected)
    assert reports == [] and locked[KEY]['faculty_id'] == 'F5'


# ── 19-23. Faculty ──────────────────────────────────────────────────────────────

def test_same_faculty_designated():
    e = plan_for(policy(group(1, [1, 2], mode='SAME_FACULTY', designated='D1')), 1)['entries'][('NSTP 001', 'Lecture')]
    assert e['faculty'] == {'status': 'designated', 'faculty_id': 'D1', 'known': []}
    _s, (locked, _r) = _pin(plan_for(policy(group(1, [1, 2], mode='SAME_FACULTY', designated='D1')), 1))
    assert locked[KEY]['lock']['faculty'] and locked[KEY]['faculty_id'] == 'D1'
    assert locked[KEY]['instructor'] == 'Dee One'


def test_same_faculty_known_from_another_members_valid_event_only():
    pol = policy(group(1, [1, 2, 3], mode='SAME_FACULTY'))
    e = plan_for(pol, 1, [live(2, fac='X'), live(3, fac='Y', room=QUAD)])['entries'][('NSTP 001', 'Lecture')]
    assert e['faculty']['status'] == 'known' and e['faculty']['faculty_id'] == 'X'   # off-slot Y ignored


def test_same_faculty_conflicting_known_faculty_is_not_chosen_and_reported():
    plan = plan_for(policy(group(1, [1, 2, 3], mode='SAME_FACULTY')), 1, [live(2, fac='X'), live(3, fac='Y')])
    _s, (locked, reports) = _pin(plan)
    assert locked[KEY]['faculty_id'] is None and locked[KEY]['lock']['faculty']
    assert [r['code'] for r in reports] == ['HC16_FACULTY']


def test_same_faculty_all_tba_stays_tba():
    plan = plan_for(policy(group(1, [1, 2], mode='SAME_FACULTY')), 1, [live(2, fac=None)])
    assert plan['entries'][('NSTP 001', 'Lecture')]['faculty']['status'] == 'tba'
    s = IntelligentScheduler()
    locked, _r = s._apply_group_pins({}, {}, [SUBJ], plan, {}, ROOMS)
    out = s._finalize_group_genes([gene(fac='F9')], plan, locked)
    assert out[0]['faculty_id'] is None and any('none is designated' in r for r in out[0]['incomplete_reason'])


def test_multiple_faculty_leaves_faculty_to_normal_selection():
    _s, (locked, _r) = _pin(plan_for(policy(group(1, [1, 2], mode='MULTIPLE_FACULTY')), 1))
    assert locked[KEY]['lock']['faculty'] is False


# ── 24-27. Occupancy: same event never blocks, outsiders do ─────────────────────

def test_same_event_occupancy_is_skipped_outsiders_are_not():
    pol = policy(group(1, [1, 2]), section=1)
    plan = plan_for(pol, 1)
    skip = IntelligentScheduler._same_event_skipper(pol, plan)
    assert skip(live(2)) is True                                         # same event: room and faculty free
    assert skip(live(9, code='GEED 001')) is False                       # outsider in the Gym
    assert skip(live(2, room=QUAD)) is False                             # member off the event
    assert IntelligentScheduler._same_event_skipper(None, plan) is None  # legacy: unchanged


def test_unscheduled_group_exempts_nothing():
    pol = policy(group(1, [1, 2], meetings=[], complete=()), section=1)
    assert IntelligentScheduler._same_event_skipper(pol, plan_for(pol, 1)) is None


# ── Finalization: unscheduled strip, extra meeting sets, room TBA ───────────────

def test_finalize_keeps_unmerged_genes_and_adds_extra_meeting_sets():
    s = IntelligentScheduler()
    plan = plan_for(policy(group(1, [1, 2], meetings=[], complete=())), 1)
    out = s._finalize_group_genes([gene()], plan, {})
    assert out[0]['start_time'] == time(7, 30) and out[0]['room_id'] == QUAD     # untouched (P7)
    assert not out[0].get('incomplete_reason')
    split = [meeting(11, day='Monday', start='09:00', end='10:30'),
             meeting(12, day='Thursday', start='13:30', end='15:00', room=QUAD)]
    plan = plan_for(policy(group(1, [1, 2], meetings=split)), 1)
    locked, _r = s._apply_group_pins({}, {}, [SUBJ], plan, {}, ROOMS)
    g = gene()
    s._reapply_locks([g], locked)
    out = s._finalize_group_genes([g], plan, locked)
    assert [(x['day'], x['start_time'], x['room_id']) for x in out] == \
        [('Monday', time(9, 0), GYM), ('Thursday', time(13, 30), QUAD)]
    s._reapply_locks(out, locked)                                         # the extra set is not reset
    assert out[1]['day'] == 'Thursday'


def test_room_tba_meeting_stays_tba():
    s = IntelligentScheduler()
    plan = plan_for(policy(group(1, [1, 2], meetings=[meeting(11, room=None)])), 1)
    locked, _r = s._apply_group_pins({}, {}, [SUBJ], plan, {}, ROOMS)
    out = s._finalize_group_genes([gene()], plan, locked)
    assert out[0]['room_id'] is None and any('room TBA' in r for r in out[0]['incomplete_reason'])


# ── 29-30. Existing Published members ──────────────────────────────────────────

def test_member_elsewhere_is_never_used_as_the_slot_nor_reported():
    pol = policy(group(1, [1, 2]))
    plan = plan_for(pol, 1, [live(2, day='Saturday', room=QUAD)])
    assert plan['entries'][('NSTP 001', 'Lecture')]['sets'][0]['days'] == ['Sunday']
    assert plan['divergent'] == []          # P7: off the merged slot is an ordinary class


# ── 38. Retrieved/historical rows take the CURRENT group slot ────────────────────

def test_retrieved_rows_use_the_current_group_slot():
    row = {'subject_code': 'NSTP 001', 'class_type': 'Lecture', 'start_time': time(7, 30), 'end_time': time(10, 30),
           'days_list': ['Saturday'], 'day': 'Saturday', 'room_id': QUAD, 'room': 'LQ-QUAD', 'faculty_id': 'H1'}
    orig = dict(row)
    rows, reports = mg.pin_rows([row], plan_for(policy(group(1, [1, 2])), 1))
    assert (rows[0]['day'], rows[0]['start_time'], rows[0]['end_time'], rows[0]['room_id']) == \
        ('Sunday', time(9, 0), time(12, 0), GYM)
    assert rows[0]['faculty_id'] == 'H1' and reports == []              # MULTIPLE: history may keep faculty
    # P7: a group with no merged slot leaves the retrieved row as it was
    rows, _ = mg.pin_rows([dict(orig)], plan_for(policy(group(1, [1, 2], meetings=[], complete=())), 1))
    assert rows[0]['start_time'] == time(7, 30) and not rows[0].get('incomplete')


# ── 33-35. Partial-meeting HC17 correction ──────────────────────────────────────

def _mon_thu_group(rules=False):
    mts = [meeting(11, day='Monday', start='09:00', end='10:30'), meeting(12, day='Thursday', start='09:00', end='10:30')]
    g = group(1, [1, 2], meetings=mts, teaching=3.0, units=3.0)
    return mg.GroupMergePolicy(mg.MergeIndex([g]),
                               load_rules={1: [{'policyid': 7, 'min_sections': 1, 'max_sections': 3}]} if rules else {})


def _load_row(section, fac, day):
    return {'employeenumber': fac, 'sectionid': section, 'subjectcode': 'NSTP 001', 'days': day,
            'start': '09:00', 'end': '10:30', 'roomid': GYM, 'hrs': 1.5, 'time_code': '0910',
            'time_range': '09:00 - 10:30', 'year_section': f'SEC-{section}', 'units': 3}


@pytest.mark.parametrize('rules', [False, True], ids=['no_policy', 'policy'])
def test_partial_meeting_faculty_get_only_the_meetings_they_teach(rules):
    pol = _mon_thu_group(rules)
    rows = [_load_row(1, 'A', 'Monday'), _load_row(2, 'A', 'Monday'),
            _load_row(1, 'B', 'Thursday'), _load_row(2, 'B', 'Thursday')]
    meta = pol.credit_rows(rows)
    by_fac = {}
    for r, m in zip(rows, meta):
        by_fac[r['employeenumber']] = by_fac.get(r['employeenumber'], 0) + m['credit']
    if rules:
        assert by_fac == {'A': 1.5, 'B': 1.5}                           # never the other's meeting
        assert all(m['partial'] for m in meta)
    else:
        assert by_fac == {'A': 3.0, 'B': 3.0}                           # P7: per section, own meetings only


def test_full_meeting_faculty_with_policy_gets_curriculum_hours_and_units_once():
    pol = _mon_thu_group(True)
    rows = [_load_row(s, 'A', d) for s in (1, 2) for d in ('Monday', 'Thursday')]
    assert round(sum(m['credit'] for m in pol.credit_rows(rows)), 2) == 3.0
    grouped = fl.group_assignments(rows, config=mg.with_policy({'hc_merge_model': 'groups'}, pol))
    assert len(grouped) == 1 and grouped[0]['units'] == 3.0               # units once, not per meeting


def test_partial_faculty_units_are_not_multiplied():
    pol = _mon_thu_group(True)
    grouped = fl.group_assignments([_load_row(1, 'A', 'Monday'), _load_row(2, 'A', 'Monday')],
                                   config=mg.with_policy({'hc_merge_model': 'groups'}, pol))
    assert len(grouped) == 1 and grouped[0]['units'] == 3.0 and round(grouped[0]['hrs'], 2) == 1.5


# ── 44-45. No NSTP prefix / legacy scope in group-mode generation ───────────────

def test_plan_ignores_subject_prefixes_and_legacy_scope():
    assert plan_for(policy(), 1)['entries'] == {}                       # NSTP without a group: nothing pinned
    g = group(1, [1, 2], code='GEED 001')                                # any subject can be a group
    assert ('GEED 001', 'Lecture') in plan_for(policy(g), 1)['entries']
    assert mg.load_generation_plan({'hc_merge_model': 'legacy', 'hc_merge_scope': 'all_subjects'}, 1, 1) == (None, None)


def test_generation_validator_uses_the_shared_policy_for_hc9():
    pol = policy(group(1, [1, 2]), section=2, rules={1: [{'policyid': 7, 'min_sections': 2, 'max_sections': 2}]})
    csp = CSPValidator(config=mg.with_policy({'hc_merge_model': 'groups'}, pol))
    existing = fl.summarize_faculty_load([{'employeenumber': 'F1', 'sectionid': 1, 'subjectcode': 'NSTP 001',
                                           'days': 'Sunday', 'start': '09:00', 'end': '12:00', 'roomid': GYM,
                                           'hrs': 3.0, 'time_code': '0912', 'time_range': '09:00 - 12:00',
                                           'year_section': 'S1'}],
                                         config=mg.with_policy({'hc_merge_model': 'groups'}, pol))
    g = gene(2, day='Sunday', start=(9, 0), end=(12, 0), room=GYM, fac='F1')
    credit, _delta = csp._group_mode_load_credit([g], {'F1': {}}, {'F1': existing})
    assert credit[id(g)] == 0.0                                          # already counted in section 1
    # P7: without a mapped policy the second section's class counts in full
    plain = policy(group(1, [1, 2]), section=2)
    csp2 = CSPValidator(config=mg.with_policy({'hc_merge_model': 'groups'}, plain))
    credit2, _ = csp2._group_mode_load_credit([g], {'F1': {}}, {'F1': existing})
    assert credit2[id(g)] == 3.0
