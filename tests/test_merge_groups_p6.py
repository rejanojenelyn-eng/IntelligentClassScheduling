"""HC16 group model — P6 Local Scheduler / Faculty request restriction rule (no database).

merge_groups.occurrence_change_restriction decides whether a Local adjustment or a
Schedule Adjustment request may change one Official occurrence. P7: a member occurrence
that IS a merged class (at its group's meeting) is shared in both faculty modes; a
member scheduled anywhere else is an ordinary class and may be adjusted.
"""
import merge_groups as mg
from test_merge_groups_p2 import GYM, QUAD, meeting, policy
from test_merge_groups_p2 import group as _group


def group(gid, sections, **kw):
    # the occurrences below are identified by timeslot ids (4-10), as stored sessions are
    kw.setdefault('meetings', [meeting(gid * 10 + 1, stid=4, etid=10)])
    return _group(gid, sections, **kw)

CODE = 'NSTP 001'


def occ(section=1, *, day='Sunday', st=4, et=10, room=GYM, fac='F1', code=CODE):
    return {'sectionid': section, 'subjectcode': code, 'daydesc': day, 'starttimeid': st, 'endtimeid': et,
            'roomid': room, 'employeenumber': fac, 'sectionname': f'S{section}', 'programcode': 'BSIT'}


def restrict(pol, o, proposed=None, **kw):
    return mg.occurrence_change_restriction(pol, o, proposed, **kw)


def test_change_detection_per_dimension():
    o = occ()
    assert mg.occurrence_changes(o, {}) == []
    assert mg.occurrence_changes(o, {'daydesc': 'Sunday', 'starttimeid': 4, 'endtimeid': 10, 'roomid': GYM,
                                     'faculty_id': 'F1'}) == []
    assert mg.occurrence_changes(o, {'daydesc': 'Monday'}) == ['day']
    assert mg.occurrence_changes(o, {'starttimeid': 5, 'endtimeid': 11}) == ['time']
    assert mg.occurrence_changes(o, {'endtimeid': '11'}) == ['time']
    assert mg.occurrence_changes(o, {'roomid': QUAD}) == ['room']
    assert mg.occurrence_changes(o, {'roomid': str(GYM)}) == []
    assert mg.occurrence_changes(o, {'faculty_id': 'F2'}) == ['faculty']
    assert mg.occurrence_changes(o, {'faculty_id': 'TBA'}) == []          # TBA is not a substitution


def test_local_change_of_a_member_occurrence_is_rejected_in_both_faculty_modes():
    for mode, designee in ((mg.MULTIPLE_FACULTY, None), (mg.SAME_FACULTY, 'F1'), (mg.SAME_FACULTY, None)):
        pol = policy(group(1, [1, 2], mode=mode, designated=designee))
        for proposed, what in (({'daydesc': 'Monday'}, ['day']), ({'starttimeid': 6, 'endtimeid': 12}, ['time']),
                               ({'roomid': QUAD}, ['room']), ({'faculty_id': 'F2'}, ['faculty'])):
            r = restrict(pol, occ(), proposed)
            assert r and r['code'] == mg.MERGED_OCCURRENCE_LOCKED and r['changes'] == what, (mode, proposed)
            assert r['group'] == 'Group 1' and r['mergegroupid'] == 1 and r['faculty_mode'] == mode
            assert "Merge Group 'Group 1'" in r['error'] and what[0] in r['error']


def test_multiple_faculty_substitution_is_rejected():
    pol = policy(group(1, [1, 2], mode=mg.MULTIPLE_FACULTY))
    r = restrict(pol, occ(fac='F1'), {'faculty_id': 'F7'})
    assert r['changes'] == ['faculty'] and 'each section with its own faculty' in r['error']


def test_unchanged_local_copy_is_allowed():
    pol = policy(group(1, [1, 2]))
    o = occ()
    assert restrict(pol, o, {'daydesc': 'Sunday', 'starttimeid': 4, 'endtimeid': 10, 'roomid': GYM,
                             'faculty_id': 'F1'}) is None


def test_request_targeting_a_member_occurrence_is_always_rejected():
    pol = policy(group(1, [1, 2]))
    r = restrict(pol, occ(), {'daydesc': None}, action='Schedule Adjustment request', targeted=True)
    assert r and r['changes'] == ['day', 'time', 'room'] and 'Schedule Adjustment request' in r['error']
    r = restrict(pol, occ(), {'roomid': QUAD}, action='Schedule Adjustment request', targeted=True)
    assert r['changes'] == ['room']
    assert 'make-up class' in r['error']


def test_member_that_is_not_a_merged_class_is_not_restricted():
    # P7: sections only allowed to merge (no merged slot) adjust freely ...
    unscheduled = group(1, [1, 2], meetings=[], complete=())
    assert restrict(policy(unscheduled), occ(), {'roomid': QUAD}) is None
    # ... and so does a member scheduled away from its group's merged slot.
    assert restrict(policy(group(1, [1, 2])), occ(room=QUAD, day='Monday'), {'roomid': GYM}) is None


def test_non_members_invalid_groups_and_legacy_are_not_restricted():
    pol = policy(group(1, [1, 2]))
    assert restrict(pol, occ(section=3), {'roomid': QUAD}) is None
    assert restrict(pol, occ(code='MATH 1'), {'roomid': QUAD}) is None
    bad = policy(group(1, [1, 2], errors=['broken']))
    assert restrict(bad, occ(), {'roomid': QUAD}) is None
    inactive = policy(group(1, [1, 2], active=False))
    assert restrict(inactive, occ(), {'roomid': QUAD}) is None
    assert restrict(None, occ(), {'roomid': QUAD}) is None                # legacy model: no policy
    disabled = mg.GroupMergePolicy(mg.MergeIndex([group(1, [1, 2])]), enabled=False)
    assert restrict(disabled, occ(), {'roomid': QUAD}) is None


def test_explanation_names_the_section_subject_group_and_meetings():
    r = restrict(policy(group(1, [1, 2], mode=mg.SAME_FACULTY)), occ(), {'daydesc': 'Monday'})
    assert r['subject'] == CODE and 'BSIT' in r['section'] and 'one faculty for every section' in r['error']
    assert 'PUP GYM' in r['error'] and 'Sunday' in r['error']


def test_make_up_requests_never_get_a_merge_exemption_in_group_mode():
    import app as app_module
    req = {'_cfg': {'hc_merge_model': 'groups', 'hc_merge_enabled': 1}, 'subjectcode': CODE,
           'employeenumber': 'F1', 'programcode': 'BSIT', 'sectionname': 'S1'}
    same_class_other_section = {'subjectcode': CODE, 'employeenumber': 'F1', 'programcode': 'BSIT',
                                'sectionname': 'S2'}
    assert app_module._request_occupant_blocks(req, same_class_other_section, 'room') is True
    assert app_module._request_occupant_blocks(req, same_class_other_section, 'faculty') is True
    assert app_module._request_occupant_blocks(req, same_class_other_section, 'section') is True
    # a TBA occupant still never blocks on faculty (unchanged rule)
    assert app_module._request_occupant_blocks(req, dict(same_class_other_section, employeenumber=None),
                                               'faculty') is False
