"""HC16 Merge Groups — P1 pure tests (no database).

Covers configuration validity, cross-curriculum subject equivalence (incl. the
controlled admin override), scheduling completeness, MergeIndex event identity and
faculty-mode rules, legacy-merge discovery, the dry run, impact preview, and the P1
boundary: no scheduling path reads Merge Groups yet and the internal model switch
defaults to 'legacy'.
"""
import os
import re

import pytest

import merge_groups as mg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LEGACY_NSTP = {'hc_merge_enabled': 1, 'hc_merge_scope': 'nstp_only', 'hc_merge_section_pairs': '[]'}


def subj(csid, code='NSTP 001', name='National Service Training Program 1', lec=3, lab=0, units=3,
         tuition=3):
    return {'curriculumsubjectid': csid, 'subjectcode': code, 'subjectname': name,
            'lecturehours': lec, 'laboratoryhours': lab, 'creditunits': units, 'tuitionhours': tuition}


# ── Internal model switch ───────────────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    (None, 'legacy'), ('', 'legacy'), ('legacy', 'legacy'), ('groups', 'groups'),
    ('GROUPS', 'groups'), (' groups ', 'groups'), ('pairs', 'legacy'), (1, 'legacy'),
])
def test_model_switch_defaults_to_legacy_unless_explicitly_groups(raw, expected):
    assert mg.merge_model({mg.MODEL_KEY: raw}) == expected
    assert mg.merge_model({}) == 'legacy'


def test_frozen_keys_cover_the_switch_and_every_legacy_key():
    assert mg.MODEL_KEY in mg.FROZEN_CONFIG_KEYS
    for key in ('hc_merge_scope', 'hc_merge_scope_subjects', 'hc_merge_section_pairs'):
        assert key in mg.FROZEN_CONFIG_KEYS
    assert 'hc_merge_enabled' not in mg.FROZEN_CONFIG_KEYS   # the HC16 on/off toggle stays editable


# ── Cross-curriculum equivalence ────────────────────────────────────────────────

def test_equivalence_reference_and_same_code():
    ref = subj(1)
    assert mg.subject_equivalence(ref, subj(1))['basis'] == 'reference'
    assert mg.subject_equivalence(ref, subj(2, code='nstp  001'))['basis'] == 'same_code'


def test_equivalence_same_name_and_hours_needs_same_units():
    ref = subj(1, code='NSTP 001')
    other = subj(2, code='NSTP 10013', name='national service training program 1')
    assert mg.subject_equivalence(ref, other)['basis'] == 'same_name_and_hours'
    diff_units = subj(3, code='NSTP 10013', name='National Service Training Program 1', units=2)
    info = mg.subject_equivalence(ref, diff_units)
    assert info['basis'] is None and info['override_eligible'] is True


def test_equivalence_different_code_and_name_is_only_override_eligible():
    info = mg.subject_equivalence(subj(1), subj(2, code='CWTS 1', name='Civic Welfare Training'))
    assert info['basis'] is None
    assert info['override_eligible'] is True
    assert any('code' in d for d in info['differences'])


@pytest.mark.parametrize('cand', [
    subj(2, lec=2, tuition=2),                 # lecture hours differ
    subj(2, lab=3, tuition=6),                 # lab hours differ
    subj(2, tuition=5),                        # teaching hours differ
    subj(2, code='NSTP 001', lab=3, tuition=6),   # even the SAME code needs the same structure
])
def test_incompatible_teaching_structure_is_never_equivalent_nor_overridable(cand):
    info = mg.subject_equivalence(subj(1), cand)
    assert info['basis'] is None
    assert info['override_eligible'] is False


def test_teaching_hours_fall_back_to_lecture_plus_lab_when_tuition_is_zero():
    assert mg.teaching_structure(subj(1, lec=2, lab=3, tuition=0)) == (2, 3, 5)
    assert mg.subject_equivalence(subj(1, lec=2, lab=3, tuition=0),
                                  subj(2, lec=2, lab=3, tuition=5))['basis'] == 'same_code'


def test_required_parts_mirror_the_generator_split():
    assert mg.required_parts(subj(1, lec=2, lab=3)) == {'Lecture': 2, 'Lab': 3}
    assert mg.required_parts(subj(1, lec=0, lab=3)) == {'Lab': 3}
    assert mg.required_parts(subj(1, lec=3, lab=0)) == {'Lecture': 3}
    assert mg.required_parts(subj(1, lec=0, lab=0)) == {'Lecture': 3.0}


# ── Configuration validity ──────────────────────────────────────────────────────

def ctx(**over):
    base = {
        'semester': {'semesterid': 1, 'ended': False},
        'subjects': {1: subj(1), 2: subj(2), 3: subj(3), 9: subj(9, code='CWTS 1', name='Civic Welfare'),
                     7: subj(7, code='MATH 1', lab=3, tuition=6)},
        'sections': {
            10: {'label': 'BSIT 1-1', 'isactive': True, 'offered': {1, 7}},
            11: {'label': 'BSIT 1-2', 'isactive': True, 'offered': {2}},
            12: {'label': 'BSA 1-1', 'isactive': True, 'offered': {3}},
            13: {'label': 'BSCS 1-1', 'isactive': True, 'offered': {9}},
            14: {'label': 'OLD 1-1', 'isactive': False, 'offered': {1}},
        },
        'timeslots': {1: '07:30', 2: '09:00', 3: '10:30', 4: '13:30', 5: '12:00'},
        'rooms': {100: {'roomid': 100, 'roomname': 'GYM', 'roomtype': 'Lecture'},
                  200: {'roomid': 200, 'roomname': 'LAB1', 'roomtype': 'Laboratory'}},
        'faculty': {'F1': {'isactive': True}, 'F2': {'isactive': True}, 'FX': {'isactive': False}},
        'active_memberships': {},
        'other_names': set(),
    }
    base.update(over)
    return base


def payload(**over):
    p = {'groupname': 'Sunday NSTP Group A', 'semesterid': 1, 'ref_curriculumsubjectid': 1,
         'faculty_mode': 'MULTIPLE_FACULTY', 'employeenumber': None, 'is_active': True,
         'members': [{'sectionid': 10, 'curriculumsubjectid': 1}, {'sectionid': 11, 'curriculumsubjectid': 2}],
         'meetings': []}
    p.update(over)
    return mg.normalize_payload(p)


def test_valid_group_without_meetings_is_configured():
    errors, members = mg.validate_group(payload(), ctx())
    assert errors == []
    assert [m['equivalence_basis'] for m in members] == ['reference', 'same_code']


def test_needs_at_least_two_distinct_sections():
    errors, _ = mg.validate_group(payload(members=[{'sectionid': 10, 'curriculumsubjectid': 1}]), ctx())
    assert any('at least 2' in e for e in errors)
    errors, _ = mg.validate_group(payload(members=[{'sectionid': 10, 'curriculumsubjectid': 1},
                                                   {'sectionid': 10, 'curriculumsubjectid': 1}]), ctx())
    assert any('only be listed once' in e for e in errors)


def test_member_subject_must_be_offered_to_that_section():
    errors, _ = mg.validate_group(payload(members=[{'sectionid': 10, 'curriculumsubjectid': 1},
                                                   {'sectionid': 11, 'curriculumsubjectid': 3}]), ctx())
    assert any('not offered' in e for e in errors)


def test_inactive_section_and_unknown_semester_are_rejected():
    errors, _ = mg.validate_group(payload(members=[{'sectionid': 10, 'curriculumsubjectid': 1},
                                                   {'sectionid': 14, 'curriculumsubjectid': 1}]), ctx())
    assert any('inactive' in e for e in errors)
    errors, _ = mg.validate_group(payload(), ctx(semester=None))
    assert any('semester' in e for e in errors)


def test_faculty_mode_and_designated_faculty_rules():
    errors, _ = mg.validate_group(payload(faculty_mode='WHATEVER'), ctx())
    assert any('Faculty mode' in e for e in errors)
    errors, _ = mg.validate_group(payload(employeenumber='F1'), ctx())            # MULTIPLE + designee
    assert any('Same Faculty' in e for e in errors)
    errors, _ = mg.validate_group(payload(faculty_mode='SAME_FACULTY', employeenumber='F1'), ctx())
    assert errors == []
    errors, _ = mg.validate_group(payload(faculty_mode='SAME_FACULTY', employeenumber='FX'), ctx())
    assert any('inactive' in e for e in errors)
    errors, _ = mg.validate_group(payload(faculty_mode='SAME_FACULTY', employeenumber='NOPE'), ctx())
    assert any('does not exist' in e for e in errors)
    assert mg.normalize_payload({'employeenumber': 'TBA'})['employeenumber'] is None


def test_override_must_be_chosen_but_its_note_is_optional():
    members = [{'sectionid': 10, 'curriculumsubjectid': 1}, {'sectionid': 13, 'curriculumsubjectid': 9}]
    errors, _ = mg.validate_group(payload(members=members), ctx())
    # not automatically equivalent: the differences stay visible and an override is required
    assert any('not automatically equivalent' in e and 'override is required' in e for e in errors)
    assert not any('justification' in e for e in errors)
    for note in (None, '', '   ', 'same'):
        m = [members[0], dict(members[1], equivalence_basis='admin_override', equivalence_note=note)]
        errors, resolved = mg.validate_group(payload(members=m), ctx())
        assert errors == [], (note, errors)
        assert resolved[1]['equivalence_basis'] == 'admin_override'
    ok = [members[0], dict(members[1], equivalence_basis='admin_override',
                           equivalence_note='CWTS 1 is the institutional NSTP-1 component for BSCS.')]
    errors, resolved = mg.validate_group(payload(members=ok), ctx())
    assert errors == []
    assert resolved[1]['equivalence_basis'] == 'admin_override'
    assert resolved[1]['equivalence_note'].startswith('CWTS 1')
    assert mg.normalize_payload({'members': [dict(members[1], equivalence_note='  ')]})['members'][0][
        'equivalence_note'] is None


def test_override_can_never_bypass_incompatible_hours():
    c = ctx()
    c['sections'][11]['offered'] = {7}
    members = [{'sectionid': 10, 'curriculumsubjectid': 1},
               {'sectionid': 11, 'curriculumsubjectid': 7, 'equivalence_basis': 'admin_override',
                'equivalence_note': 'Please merge these even though the hours differ.'}]
    errors, _ = mg.validate_group(payload(members=members), c)
    assert any('incompatible teaching hours' in e for e in errors)


def test_automatic_basis_wins_over_a_needless_override_request():
    members = [{'sectionid': 10, 'curriculumsubjectid': 1},
               {'sectionid': 11, 'curriculumsubjectid': 2, 'equivalence_basis': 'admin_override',
                'equivalence_note': 'not needed but provided anyway'}]
    errors, resolved = mg.validate_group(payload(members=members), ctx())
    assert errors == [] and resolved[1]['equivalence_basis'] == 'same_code'
    assert resolved[1]['equivalence_note'] is None


def test_section_subject_cannot_be_in_two_active_groups():
    taken = {(11, 2): 'Other group'}
    errors, _ = mg.validate_group(payload(), ctx(active_memberships=taken))
    assert any('Other group' in e for e in errors)
    by_code = {(11, 'NSTP 001'): 'Bridging group'}   # same code via another csid (bridging curriculum)
    errors, _ = mg.validate_group(payload(), ctx(active_memberships=by_code))
    assert any('Bridging group' in e for e in errors)
    errors, _ = mg.validate_group(payload(is_active=False), ctx(active_memberships=taken))
    assert errors == []   # an inactive group may overlap until it is activated


def test_group_name_rules():
    assert any('name is required' in e for e in mg.validate_group(payload(groupname='  '), ctx())[0])
    errors, _ = mg.validate_group(payload(), ctx(other_names={'sunday nstp group a'}))
    assert any('already named' in e for e in errors)


def test_meeting_structure_validation():
    bad = [{'class_type': 'Seminar', 'daydesc': 'Funday', 'starttimeid': 1, 'endtimeid': 2},
           {'class_type': 'Lecture', 'daydesc': 'Sunday', 'starttimeid': 99, 'endtimeid': 2},
           {'class_type': 'Lecture', 'daydesc': 'Sunday', 'starttimeid': 2, 'endtimeid': 1},
           {'class_type': 'Lecture', 'daydesc': 'Sunday', 'starttimeid': 1, 'endtimeid': 3, 'roomid': 999}]
    errors = mg.validate_meeting_structure(mg.normalize_payload({'meetings': bad})['meetings'], ctx())
    joined = ' | '.join(errors)
    assert 'class type' in joined and 'valid day' in joined
    assert 'existing time slots' in joined and 'after start' in joined and 'does not exist' in joined
    overlap = [{'class_type': 'Lecture', 'daydesc': 'Sunday', 'starttimeid': 1, 'endtimeid': 3},
               {'class_type': 'Lecture', 'daydesc': 'Sunday', 'starttimeid': 2, 'endtimeid': 4}]
    assert any('overlaps' in e for e in mg.validate_meeting_structure(
        mg.normalize_payload({'meetings': overlap})['meetings'], ctx()))


# ── Scheduling completeness (separate from configuration validity) ──────────────

BLOCKS = {('07:30', '10:30'), ('07:30', '09:00'), ('09:00', '10:30'), ('10:30', '13:30')}


def mt(day, start, end, ctype='Lecture', room=100):
    return {'class_type': ctype, 'daydesc': day, 'start': start, 'end': end, 'roomid': room}


def test_states_unscheduled_incomplete_scheduled():
    rooms = ctx()['rooms']
    assert mg.scheduling_completeness(subj(1), [], valid_blocks=BLOCKS)['state'] == 'UNSCHEDULED'
    half = mg.scheduling_completeness(subj(1), [mt('Sunday', '07:30', '09:00')], valid_blocks=BLOCKS, rooms=rooms)
    assert half['state'] == 'INCOMPLETE' and any('1.5h scheduled of 3h' in i for i in half['issues'])
    full = mg.scheduling_completeness(subj(1), [mt('Sunday', '07:30', '10:30')], valid_blocks=BLOCKS, rooms=rooms)
    assert full['state'] == 'SCHEDULED' and full['issues'] == []
    split = mg.scheduling_completeness(subj(1), [mt('Monday', '07:30', '09:00'), mt('Thursday', '07:30', '09:00')],
                                       valid_blocks=BLOCKS, rooms=rooms)
    assert split['state'] == 'SCHEDULED'


def test_completeness_checks_time_blocks_lab_rooms_and_types():
    rooms = ctx()['rooms']
    off_grid = mg.scheduling_completeness(subj(1), [mt('Sunday', '08:00', '11:00')], valid_blocks=BLOCKS, rooms=rooms)
    assert any('HC6' in i for i in off_grid['issues'])
    assert mg.scheduling_completeness(subj(1), [mt('Sunday', '08:00', '11:00')], valid_blocks=None,
                                      rooms=rooms)['state'] == 'SCHEDULED'   # HC6 switched off
    lab_subject = subj(1, lec=3, lab=3, tuition=6)
    lab_in_lecture_room = mg.scheduling_completeness(
        lab_subject, [mt('Monday', '07:30', '10:30'), mt('Tuesday', '07:30', '10:30', 'Lab', 100)],
        valid_blocks=BLOCKS, rooms=rooms)
    assert any('Laboratory room' in i for i in lab_in_lecture_room['issues'])
    ok = mg.scheduling_completeness(
        lab_subject, [mt('Monday', '07:30', '10:30'), mt('Tuesday', '07:30', '10:30', 'Lab', 200)],
        valid_blocks=BLOCKS, rooms=rooms)
    assert ok['state'] == 'SCHEDULED'
    no_lab_hours = mg.scheduling_completeness(subj(1), [mt('Sunday', '07:30', '10:30', 'Lab', 200)],
                                              valid_blocks=BLOCKS, rooms=rooms)
    assert any('no Lab hours' in i for i in no_lab_hours['issues'])


def test_room_tba_is_a_warning_not_incompleteness():
    out = mg.scheduling_completeness(subj(1), [mt('Sunday', '07:30', '10:30', room=None)], valid_blocks=BLOCKS)
    assert out['state'] == 'SCHEDULED'
    assert any('TBA' in w for w in out['warnings'])


# ── MergeIndex: merged-event identity ───────────────────────────────────────────

def group(gid, sections, *, code='NSTP 001', meetings=(('Sunday', '07:30', '10:30', 100),),
          mode='MULTIPLE_FACULTY', designated=None, active=True, state='SCHEDULED', errors=()):
    return {'mergegroupid': gid, 'groupname': f'G{gid}', 'is_active': active, 'config_errors': list(errors),
            'faculty_mode': mode, 'employeenumber': designated, 'state': state,
            'members': [{'sectionid': s, 'subjectcode': code} for s in sections],
            'meetings': [{'mergegroupmeetingid': gid * 100 + i, 'daydesc': d, 'start': s, 'end': e, 'roomid': r}
                         for i, (d, s, e, r) in enumerate(meetings)]}


def occ(section, *, code='NSTP 001', day='Sunday', start='07:30', end='10:30', room=100, fac=None):
    return {'sectionid': section, 'subject_code': code, 'day': day, 'start_time': start,
            'end_time': end, 'room_id': room, 'faculty_id': fac}


def test_two_section_merge():
    idx = mg.MergeIndex([group(5, [1, 2])])
    assert idx.event_of(occ(1)) == (5, 500)
    assert idx.same_event(occ(1, fac='F1'), occ(2, fac='F2'))


def test_three_and_more_sections_need_no_pairwise_configuration():
    idx = mg.MergeIndex([group(5, [1, 2, 3, 4, 6])])
    sections = [occ(s) for s in (1, 2, 3, 4, 6)]
    assert all(idx.same_event(a, b) for i, a in enumerate(sections) for b in sections[i + 1:])


def test_outsider_wrong_room_wrong_time_and_same_section_are_never_the_event():
    idx = mg.MergeIndex([group(5, [1, 2, 3])])
    assert not idx.same_event(occ(1), occ(9))                           # not a member (any subject)
    assert not idx.same_event(occ(1), occ(9, code='GEED 001'))          # unrelated class, same room/time
    assert not idx.same_event(occ(1), occ(2, room=101))                 # wrong room
    assert not idx.same_event(occ(1), occ(2, start='08:00', end='11:00'))  # wrong time
    assert not idx.same_event(occ(1), occ(2, day='Saturday'))           # wrong day
    assert not idx.same_event(occ(1), occ(1))                           # same section never "merges"
    # P7: a member off the group's slot is an ordinary class of its own section.
    assert idx.classify(occ(2, room=101)) == 'separate'
    assert idx.classify(occ(9)) is None


def test_same_subject_in_different_groups_or_no_group_is_not_merged():
    idx = mg.MergeIndex([group(5, [1, 2]), group(6, [3, 4])])
    assert not idx.same_event(occ(1), occ(3))
    assert mg.MergeIndex([]).event_of(occ(1)) is None


def test_inactive_and_invalid_groups_are_ignored():
    assert len(mg.MergeIndex([group(5, [1, 2], active=False)])) == 0
    assert len(mg.MergeIndex([group(5, [1, 2], errors=['broken'])])) == 0
    assert not mg.MergeIndex([group(5, [1, 2], active=False)]).same_event(occ(1), occ(2))


def test_every_stored_meeting_is_a_merged_event_even_in_an_incomplete_group():
    # P7: a subject may merge only some of its meetings — each stored meeting is a
    # merged class on its own; completeness only decides what generation pins.
    idx = mg.MergeIndex([group(5, [1, 2], state='INCOMPLETE')])
    assert idx.event_of(occ(1)) == (5, 500)
    assert idx.same_event(occ(1), occ(2))
    # A group with no meetings (allowed in Settings, not merged yet) merges nothing.
    empty = mg.MergeIndex([group(5, [1, 2], meetings=())])
    assert empty.classify(occ(1)) == 'separate' and not empty.same_event(occ(1), occ(2))


@pytest.mark.parametrize('fa,fb,ok', [
    ('A', 'A', True), ('A', None, True), (None, 'TBA', True), ('TBA', 'TBA', True), ('A', 'B', False),
])
def test_same_faculty_mode_tba_matrix(fa, fb, ok):
    idx = mg.MergeIndex([group(5, [1, 2], mode='SAME_FACULTY')])
    assert idx.same_event(occ(1, fac=fa), occ(2, fac=fb)) is ok


def test_designated_faculty_must_match_every_known_faculty():
    idx = mg.MergeIndex([group(5, [1, 2], mode='SAME_FACULTY', designated='A')])
    assert idx.same_event(occ(1, fac='A'), occ(2, fac=None))
    assert not idx.same_event(occ(1, fac='B'), occ(2, fac='B'))


def test_multiple_faculty_mode_allows_different_faculty():
    idx = mg.MergeIndex([group(5, [1, 2], mode='MULTIPLE_FACULTY')])
    assert idx.same_event(occ(1, fac='A'), occ(2, fac='B'))


def test_identity_accepts_time_objects_and_string_room_ids():
    from datetime import time
    idx = mg.MergeIndex([group(5, [1, 2])])
    a = {'section_id': 1, 'subjectcode': 'nstp 001', 'daydesc': 'Sunday', 'start_time': time(7, 30),
         'end_time': time(10, 30), 'roomid': '100'}
    assert idx.event_of(a) == (5, 500)


# ── Discovery of legacy merges ──────────────────────────────────────────────────

def docc(section, *, sem=1, code='NSTP 001', day='Sunday', start='07:30', end='10:30', room=100, roomname='GYM',
         fac='F1', prog=None, name=None, csid=None):
    return {'semesterid': sem, 'sectionid': section, 'sectionname': name or f'S{section}', 'programcode': prog or 'BSIT',
            'yearlevel': 1, 'curriculumsubjectid': csid or section * 10, 'subjectcode': code,
            'subjectname': 'NSTP 1', 'lecturehours': 3, 'laboratoryhours': 0, 'creditunits': 3, 'tuitionhours': 3,
            'day': day, 'starttimeid': 1, 'endtimeid': 3, 'start_time': start, 'end_time': end,
            'roomid': room, 'roomname': roomname, 'faculty_id': fac, 'origin': 'Official'}


def test_discovery_proposes_one_group_for_a_legacy_merge_of_three_sections():
    out = mg.discover_candidates([docc(1), docc(2), docc(3)], LEGACY_NSTP)
    assert len(out['candidates']) == 1
    c = out['candidates'][0]
    assert sorted(m['sectionid'] for m in c['members']) == [1, 2, 3]
    assert c['faculty_mode'] == 'SAME_FACULTY' and c['employeenumber'] == 'F1'
    assert c['meetings'][0]['roomid'] == 100 and not c['issues']


def test_discovery_flags_overflow_venues_and_infers_multiple_faculty():
    out = mg.discover_candidates([docc(1, fac='F1'), docc(2, room=200, roomname='QUAD', fac='F2')], LEGACY_NSTP)
    c = out['candidates'][0]
    assert c['faculty_mode'] == 'MULTIPLE_FACULTY'
    assert c['meetings'][0]['roomid'] is None
    assert any('different rooms' in i for i in c['issues'])


def test_discovery_ignores_what_the_legacy_rule_does_not_accept():
    assert mg.discover_candidates([docc(1, code='GEED 001'), docc(2, code='GEED 001')],
                                  LEGACY_NSTP)['candidates'] == []        # out of legacy scope
    assert mg.discover_candidates([docc(1), docc(2, start='08:00', end='11:00')],
                                  LEGACY_NSTP)['candidates'] == []        # partial overlap
    pairs = dict(LEGACY_NSTP, hc_merge_section_pairs='[["BSIT-S1","BSIT-S2"],["BSIT-S1","BSIT-S3"]]')
    out = mg.discover_candidates([docc(1), docc(2), docc(3)], pairs)        # B-C pair missing
    assert out['candidates'] == [] and out['notes']


def test_discovery_flags_sections_claimed_by_two_candidates():
    rows = [docc(1), docc(2), docc(1, day='Saturday'), docc(3, day='Saturday')]
    cands = mg.discover_candidates(rows, LEGACY_NSTP)['candidates']
    assert len(cands) == 2 and all(c['conflicts_with'] for c in cands)


# ── Dry run and impact ──────────────────────────────────────────────────────────

def test_dry_run_reports_legacy_merges_that_would_become_conflicts():
    rows = [docc(1, fac='F1'), docc(2, fac='F1')]
    report = mg.dry_run(rows, LEGACY_NSTP, mg.MergeIndex([]))
    assert {r['rule'] for r in report['new_conflicts']} == {'HC10', 'HC11'}
    assert all('merge group' in r['reason'] for r in report['new_conflicts'])
    grouped = mg.dry_run(rows, LEGACY_NSTP, mg.MergeIndex([group(5, [1, 2], mode='SAME_FACULTY')]))
    assert grouped['new_conflicts'] == []


def test_dry_run_unrelated_class_in_the_event_room_is_not_a_change():
    rows = [docc(1), docc(2), docc(9, code='GEED 001', fac='F9')]
    report = mg.dry_run(rows, LEGACY_NSTP, mg.MergeIndex([group(5, [1, 2], mode='SAME_FACULTY')]))
    # GEED vs NSTP in the same room is a conflict under BOTH models, so it is not a change.
    assert report['new_conflicts'] == []


def test_dry_run_reports_faculty_mode_violations_and_never_divergence():
    rows = [docc(1, fac='F1'), docc(2, fac='F2'), docc(3, room=200, fac='F1')]
    idx = mg.MergeIndex([group(5, [1, 2, 3], mode='SAME_FACULTY')])
    report = mg.dry_run(rows, LEGACY_NSTP, idx)
    # P7: section 3 off the group's slot is an ordinary class, not an issue.
    assert report['summary']['diverged'] == 0 and report['member_issues'] == []
    assert report['summary']['faculty_mode_violations'] == 1


def test_section_conflicts_are_outside_the_dry_run_comparison():
    rows = [docc(1), docc(1, code='GEED 001')]   # same section, two classes at once: HC12 in both models
    assert mg.dry_run(rows, LEGACY_NSTP, mg.MergeIndex([]))['new_conflicts'] == []


def test_compare_impact_reports_only_losing_merge():
    rows = [docc(1), docc(2)]
    before = mg.MergeIndex([group(5, [1, 2])])
    moved = mg.MergeIndex([group(5, [1, 2], meetings=(('Saturday', '07:30', '10:30', 100),))])
    out = mg.compare_impact(rows, before, moved)
    # P7: moving the merged slot makes the old occurrences ordinary classes (lose the merge).
    assert out['newly_diverging'] == [] and len(out['losing_merge']) == 2
    removed = mg.compare_impact(rows, before, mg.MergeIndex([]))
    assert len(removed['losing_merge']) == 2 and removed['newly_diverging'] == []
    unchanged = mg.compare_impact(rows, before, before)
    assert unchanged == {'newly_diverging': [], 'losing_merge': []}


# ── Boundaries (updated for P2: HC16 group mode is wired into conflict paths) ────

@pytest.mark.parametrize('path', [
    'database.py',
    'constraints/context_conflicts.py', 'constraints/constraint_service.py',
    'static/js/ACAD HEAD/manualEditor.acad.js', 'static/js/ACAD HEAD/manualEditor.acad2.js',
    'templates/academic/manualScheduleEditor.html',
])
def test_config_and_editor_paths_do_not_read_merge_groups_yet(path):
    """Config loading and the Manual Editor scripts are untouched (Manual Editor is P5).
    (faculty_load consumes the shared policy since P3 — see the P3 tests.)"""
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        src = f.read()
    # (?<!\w) keeps unrelated identifiers such as scheduler's _counted_merge_groups out.
    pattern = r'(?<!\w)merge_groups\b|\bmerge_group_(member|meeting)\b|public\.merge_group\b|hc_merge_model'
    assert not re.search(pattern, src), re.search(pattern, src).group(0)


@pytest.mark.parametrize('path', ['app.py', 'scheduler.py', 'faculty_load.py',
                                  'constraints/hard_constraints.py', 'constraints/registry.py'])
def test_only_merge_groups_reads_group_tables_and_resolves_membership(path):
    """Group membership/event resolution exists once (merge_groups.MergeIndex). No other
    module queries the group tables or re-implements the resolution."""
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        src = f.read()
    assert not re.search(r'\bFROM\s+(public\.)?merge_group', src, re.I)
    assert not re.search(r'\bJOIN\s+(public\.)?merge_group', src, re.I)
    assert 'class MergeIndex' not in src and 'def same_event' not in src


def test_schema_migration_is_additive_and_leaves_existing_tables_alone():
    sql = mg.schema_sql()
    # The only DROPs allowed: swapping the merge_group_member override CHECK for its
    # audit-only form (note optional), and widening merge_group's origin CHECK for
    # 'editor' (P7). Nothing else is ever dropped.
    allowed = ('ALTER TABLE public.merge_group_member DROP CONSTRAINT chk_mgm_override_audit;',
               'ALTER TABLE public.merge_group DROP CONSTRAINT chk_mg_origin;')
    rest = sql
    for a in allowed:
        assert sql.count(a) == 1
        rest = rest.replace(a, '')
    assert 'DROP ' not in rest.upper().replace('DROPPING', '')
    for table in ('schedule_sessions', 'schedule_version', 'mergedclass', 'scheduler_config'):
        assert f'ALTER TABLE public.{table}' not in sql
    for table in ('merge_group', 'merge_group_member', 'merge_group_meeting'):
        assert f'CREATE TABLE IF NOT EXISTS public.{table} ' in sql
    assert 'uq_mgm_active_section_subject' in sql
