"""HC16 group mode — P2 validation and conflict integration (no real database).

Group mode = scheduler_config hc_merge_model='groups' + a merge_groups.GroupMergePolicy
(built here from in-memory groups and injected the same way the app injects it).
Covers merged-event identity (per meeting), faculty modes incl. TBA, HC10/HC11
exemptions only for the same usable event, HC16 consistency violations, HC12 never
exempt, Lecture+Lab and multi-meeting groups, the cross-schedule (HC15), Local,
request and carried-forward paths, and that legacy mode is unchanged.
"""
from datetime import time

import pytest

import app as app_module
import database
import faculty_load
import merge_groups as mg
from scheduler import CSPValidator

# Everything except HC10/HC11/HC12/HC16 switched off so assertions stay focused.
QUIET = {'hc_faculty_load_enabled': 0, 'hc_weekend_enabled': 0, 'hc_time_blocks_enabled': 0,
         'hc_day_pairing_enabled': 0, 'hc_faculty_spec_enabled': 0, 'hc_lab_session_enabled': 0,
         'hc_capacity_enabled': 0, 'hc_merge_enabled': 1}
LEGACY = dict(QUIET, hc_merge_scope='nstp_only', hc_merge_section_pairs='[]')
GROUPS_BASE = dict(LEGACY, hc_merge_model='groups')

SUN = 'Sunday'
GYM, QUAD, LAB = 10, 11, 12


def meeting(mid, day=SUN, start='09:00', end='12:00', room=GYM, ctype='Lecture', stid=None, etid=None):
    return {'mergegroupmeetingid': mid, 'class_type': ctype, 'daydesc': day, 'start': start, 'end': end,
            'roomid': room, 'roomname': {GYM: 'PUP GYM', QUAD: 'LQ-QUAD', LAB: 'LAB 1'}.get(room),
            'starttimeid': stid, 'endtimeid': etid}


def group(gid, sections, *, code='NSTP 001', meetings=None, mode='MULTIPLE_FACULTY', designated=None,
          active=True, errors=(), complete=('Lecture',), required=None):
    meetings = [meeting(gid * 10 + 1)] if meetings is None else meetings
    return {'mergegroupid': gid, 'groupname': f'Group {gid}', 'is_active': active,
            'config_errors': list(errors), 'faculty_mode': mode, 'employeenumber': designated,
            'members': [{'sectionid': s, 'subjectcode': code, 'label': f'SEC-{s}'} for s in sections],
            'meetings': meetings,
            'completeness': {'complete_types': list(complete),
                             'required': dict(required or {'Lecture': 3.0})},
            'state': 'SCHEDULED' if set(required or {'Lecture': 3.0}) <= set(complete) and meetings else 'INCOMPLETE'}


def policy(*groups, section=None):
    return mg.GroupMergePolicy(mg.MergeIndex(list(groups)), default_section_id=section)


def cfg(pol):
    return mg.with_policy(dict(GROUPS_BASE), pol)


def gene(section, *, code='NSTP 001', day=SUN, days=None, start=(9, 0), end=(12, 0), room=GYM, fac='F1',
         ctype=None):
    g = {'subject_code': code, 'faculty_id': fac, 'room_id': room, 'section_id': section,
         'course': 'BSIT', 'section_name': f'S{section}', 'day': (days or [day])[0], 'days_list': days or [day],
         'start_time': time(*start), 'end_time': time(*end)}
    if ctype:
        g['class_type'] = ctype
    return g


def run(pol, genes, base=None):
    viols = CSPValidator(config=mg.with_policy(dict(base or GROUPS_BASE), pol)).validate(genes, {})
    return viols


def rules(viols):
    return sorted(v['rule'] for v in viols if v.get('severity') != 'warning')


def codes(viols):
    return sorted(v.get('code') for v in viols if v['rule'] == 'HC16')


# ── 1-3. Same event for 2, 3 and 4+ sections ────────────────────────────────────

@pytest.mark.parametrize('n', [2, 3, 5])
def test_members_of_the_same_event_share_room_and_faculty(n):
    secs = list(range(1, n + 1))
    viols = run(policy(group(1, secs)), [gene(s) for s in secs])
    assert rules(viols) == [] and codes(viols) == []


# ── 4-9. Faculty modes ───────────────────────────────────────────────────────────

def test_same_faculty_a_plus_a_is_compatible():
    viols = run(policy(group(1, [1, 2], mode='SAME_FACULTY')), [gene(1, fac='A'), gene(2, fac='A')])
    assert rules(viols) == [] and codes(viols) == []


@pytest.mark.parametrize('fa,fb,tba_warnings', [('A', None, 1), (None, 'TBA', 2)])
def test_same_faculty_with_tba_is_compatible_but_incomplete(fa, fb, tba_warnings):
    viols = run(policy(group(1, [1, 2], mode='SAME_FACULTY')), [gene(1, fac=fa), gene(2, fac=fb)])
    assert rules(viols) == []                                     # nothing blocks, room shared
    tba = [v for v in viols if v.get('code') == 'HC16_FACULTY_TBA']
    assert len(tba) == tba_warnings
    assert all(v['severity'] == 'warning' and v['merge_state'] == 'incomplete'
               and not v['blocks_publish'] for v in tba)


def test_same_faculty_a_plus_b_is_an_hc16_faculty_violation_and_never_shares_the_room():
    pol = policy(group(1, [1, 2], mode='SAME_FACULTY'))
    viols = run(pol, [gene(1, fac='A'), gene(2, fac='B')])
    assert 'HC11' in rules(viols)                                 # not one event -> room conflict
    conflicts = pol.faculty_conflicts([gene(1, fac='A')], [gene(2, fac='B')])
    assert [v['code'] for v in conflicts] == ['HC16_FACULTY']
    assert conflicts[0]['merge_state'] == 'invalid' and conflicts[0]['blocks_publish']
    assert 'A' in conflicts[0]['detail'] and 'B' in conflicts[0]['detail']
    # TBA never makes two different known faculty compatible:
    assert pol.faculty_conflicts([gene(1, fac='A')], [gene(2, fac='B'), gene(3, fac=None)])


def test_designated_faculty_mismatch_is_reported():
    viols = run(policy(group(1, [1, 2], mode='SAME_FACULTY', designated='A')),
                [gene(1, fac='B'), gene(2, fac='B')])
    assert codes(viols).count('HC16_FACULTY') == 2
    assert 'HC11' in rules(viols)


def test_multiple_faculty_mode_allows_different_faculty():
    viols = run(policy(group(1, [1, 2, 3], mode='MULTIPLE_FACULTY')),
                [gene(1, fac='A'), gene(2, fac='B'), gene(3, fac='C')])
    assert rules(viols) == [] and codes(viols) == []


# ── 10-12. No group / different groups / different meetings ─────────────────────

def test_same_subject_and_time_without_a_group_conflicts():
    viols = run(policy(), [gene(1), gene(2)])
    assert rules(viols) == ['HC10', 'HC11']


def test_same_subject_in_different_groups_conflicts():
    viols = run(policy(group(1, [1, 2]), group(2, [3, 4], meetings=[meeting(21)])), [gene(1), gene(3)])
    assert rules(viols) == ['HC10', 'HC11']


def test_same_group_but_different_meeting_is_not_the_same_event():
    mon_thu = [meeting(11, day='Monday', start='09:00', end='10:30'),
               meeting(12, day='Thursday', start='09:00', end='10:30')]
    pol = policy(group(1, [1, 2], meetings=mon_thu))
    a_mon = gene(1, day='Monday', start=(9, 0), end=(10, 30))
    b_thu = gene(2, day='Thursday', start=(9, 0), end=(10, 30))
    assert pol.index.event_of(a_mon) == (1, 11) and pol.index.event_of(b_thu) == (1, 12)
    assert not pol.same_event(a_mon, b_thu, day='Monday')
    assert not pol.same_event(a_mon, b_thu, day='Thursday')


# ── 13-16. Correct group, wrong day / start / end / room ────────────────────────

@pytest.mark.parametrize('bad', [
    dict(day='Saturday'), dict(start=(9, 30)), dict(end=(11, 30)), dict(room=QUAD),
], ids=['wrong_day', 'wrong_start', 'wrong_end', 'wrong_room'])
def test_wrong_slot_member_is_an_ordinary_class_and_gets_no_exemption(bad):
    # P7 "allowed" model: off the group's slot is not an HC16 error — it is simply not
    # merged, so the usual HC10/HC11 overlaps apply.
    viols = run(policy(group(1, [1, 2])), [gene(1), gene(2, **bad)])
    assert codes(viols) == []
    if 'room' in bad:
        assert 'HC10' in rules(viols)        # same faculty, two rooms, same time: double-booked
    elif 'day' not in bad:
        assert {'HC10', 'HC11'} <= set(rules(viols))


# ── 17-18. Inactive / invalid groups ────────────────────────────────────────────

@pytest.mark.parametrize('kw', [dict(active=False), dict(errors=['broken'])], ids=['inactive', 'invalid'])
def test_inactive_or_invalid_groups_never_exempt(kw):
    viols = run(policy(group(1, [1, 2], **kw)), [gene(1), gene(2)])
    assert rules(viols) == ['HC10', 'HC11'] and codes(viols) == []


# ── 19-21. Outsiders and HC12 ───────────────────────────────────────────────────

def test_outsider_using_the_merged_room_conflicts():
    viols = run(policy(group(1, [1, 2, 3])), [gene(1), gene(2), gene(3), gene(9, code='GEED 001', fac='G')])
    room = [v for v in viols if v['rule'] == 'HC11']
    assert len(room) == 3 and all('GEED 001' in v['subject'] for v in room)


def test_outsider_overlapping_the_merged_faculty_conflicts():
    viols = run(policy(group(1, [1, 2], mode='SAME_FACULTY')),
                [gene(1, fac='X'), gene(2, fac='X'), gene(9, code='GEED 001', room=QUAD, fac='X')])
    assert [v['rule'] for v in viols if v['rule'] == 'HC10'] == ['HC10', 'HC10']


def test_hc12_is_never_waived_for_a_merged_section():
    viols = run(policy(group(1, [1, 2])), [gene(1), gene(2), gene(1, code='GEED 001', room=QUAD, fac='G')])
    assert 'HC12' in rules(viols)


# ── 22-23. Lecture + Lab, and multiple meetings ─────────────────────────────────

def test_lecture_and_lab_meetings_are_separate_events():
    mts = [meeting(11, day='Monday', start='09:00', end='12:00', room=GYM, ctype='Lecture'),
           meeting(12, day='Tuesday', start='09:00', end='12:00', room=LAB, ctype='Lab')]
    pol = policy(group(1, [1, 2], code='CHEM 1', meetings=mts, complete=('Lecture', 'Lab'),
                       required={'Lecture': 3.0, 'Lab': 3.0}))
    lec = [gene(s, code='CHEM 1', day='Monday', room=GYM, ctype='Lecture') for s in (1, 2)]
    lab = [gene(s, code='CHEM 1', day='Tuesday', room=LAB, ctype='Lab') for s in (1, 2)]
    assert rules(run(pol, lec + lab)) == [] and codes(run(pol, lec + lab)) == []
    # a lab held in the lecture room is not the lab event (just an ordinary class)
    pol2 = policy(group(1, [1, 2], code='CHEM 1', meetings=mts, complete=('Lecture', 'Lab'),
                        required={'Lecture': 3.0, 'Lab': 3.0}))
    off = gene(1, code='CHEM 1', day='Tuesday', room=GYM, ctype='Lab')
    assert pol2.index.event_of(off, day='Tuesday') is None
    assert codes(run(pol2, [off])) == []


def test_multi_day_class_is_judged_meeting_by_meeting():
    mts = [meeting(11, day='Monday', start='09:00', end='10:30'),
           meeting(12, day='Thursday', start='09:00', end='10:30')]
    pol = policy(group(1, [1, 2], meetings=mts))
    both = [gene(s, days=['Monday', 'Thursday'], start=(9, 0), end=(10, 30)) for s in (1, 2)]
    assert rules(run(pol, both)) == [] and codes(run(pol, both)) == []
    # Section 2 meets in another room on Thursday only: Monday stays merged, Thursday is
    # an ordinary class (its overlaps are the usual HC10/HC11, on Thursday only).
    b = dict(both[1])
    split = [both[0], dict(b, days_list=['Monday']), dict(b, days_list=['Thursday'], day='Thursday', room_id=QUAD)]
    viols = run(pol, split)
    assert codes(viols) == []
    assert all('Thursday' in v.get('normalized_days', []) for v in viols if v['rule'] in ('HC10', 'HC11'))


# ── 24. Incomplete / unscheduled groups ─────────────────────────────────────────

def test_group_without_meetings_is_only_an_allowance_and_never_blocks():
    # P7: sections allowed to merge in Settings (no merged slot yet) are ordinary classes.
    viols = run(policy(group(1, [1, 2], meetings=[], complete=())), [gene(1), gene(2)])
    assert codes(viols) == []
    assert rules(viols) == ['HC10', 'HC11']      # no merged slot -> nothing is exempt


def test_a_partial_merged_meeting_is_a_merged_class():
    # P7: a subject may merge only one of its meetings (1.5h of a 3h lecture here).
    half = [meeting(11, start='09:00', end='10:30')]
    viols = run(policy(group(1, [1, 2], meetings=half, complete=())),
                [gene(s, start=(9, 0), end=(10, 30)) for s in (1, 2)])
    assert codes(viols) == [] and rules(viols) == []


# ── 30-33. Legacy unchanged; no legacy rule leaks into group mode ───────────────

def test_legacy_mode_ignores_any_injected_policy_and_behaves_as_before():
    pol = policy()                                            # would exempt nothing
    a, b = gene(1, fac='A'), gene(2, fac='B')                 # NSTP, different faculty, same room
    legacy = CSPValidator(config=dict(LEGACY)).validate([a, b], {})
    legacy_with_pol = CSPValidator(config=mg.with_policy(dict(LEGACY), pol)).validate([a, b], {})
    assert rules(legacy) == rules(legacy_with_pol) == []      # legacy NSTP merge still exempts the room
    assert codes(legacy_with_pol) == []
    assert mg.policy_from_config(LEGACY) is None


def test_nstp_shared_faculty_exemption_is_legacy_only():
    a = gene(1, code='NSTP 001', fac='X')
    b = gene(2, code='NSTP 002', room=QUAD, fac='X')          # different NSTP subject, same faculty
    assert 'HC10' not in rules(CSPValidator(config=dict(LEGACY)).validate([a, b], {}))
    assert 'HC10' in rules(run(policy(), [a, b]))


def test_group_mode_has_no_nstp_prefix_rule():
    for code in ('NSTP 001', 'OU 101'):
        assert rules(run(policy(), [gene(1, code=code), gene(2, code=code)])) == ['HC10', 'HC11']


def test_group_mode_ignores_legacy_scope_and_section_pairs():
    base = dict(GROUPS_BASE, hc_merge_scope='all_subjects', hc_merge_scope_subjects='["IT 101"]',
                hc_merge_section_pairs='[["BSIT-S1","BSIT-S2"]]')
    viols = run(policy(), [gene(1, code='IT 101'), gene(2, code='IT 101')], base=base)
    assert rules(viols) == ['HC10', 'HC11']


def test_group_mode_without_an_injected_policy_fails_closed():
    viols = CSPValidator(config=dict(GROUPS_BASE)).validate([gene(1), gene(2)], {})
    assert rules(viols) == ['HC10', 'HC11'] and codes(viols) == []


def test_validator_delegates_the_decision_to_the_shared_policy(monkeypatch):
    calls = []
    pol = policy(group(1, [1, 2]))
    real = pol.index.same_event
    monkeypatch.setattr(pol.index, 'same_event', lambda *a, **k: calls.append(1) or real(*a, **k))
    run(pol, [gene(1), gene(2)])
    assert calls                                  # HC10/HC11 asked MergeIndex, nothing re-derived


def test_hc17_load_counting_is_untouched_by_group_mode():
    # P2 boundary: HC9/HC17 merged-load still uses the legacy decision in both modes.
    v = CSPValidator(config=cfg(policy()))
    assert v.is_valid_merge(gene(1), gene(2)) is faculty_load.is_valid_merge(gene(1), gene(2), LEGACY)


# ── 27. Cross-schedule (HC15) ───────────────────────────────────────────────────

class _CrossCursor:
    def __init__(self, published):
        self.published, self._pending = published, None

    def execute(self, query, params=None):
        if 'FROM semester' in query:
            self._pending = {'semesterid': 1}
        elif 'FROM   schedule_sessions ss' in query:
            self._pending = self.published
        else:
            self._pending = []

    def fetchone(self):
        return self._pending

    def fetchall(self):
        return self._pending or []

    def close(self):
        pass


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, **k):
        return self.cur

    def close(self):
        pass

    def commit(self):
        pass


def _published(section, *, code='NSTP 001', room=GYM, fac='F1', start=time(9, 0), end=time(12, 0)):
    return {'roomid': room, 'faculty_id': fac, 'sectionid': section, 'day': SUN, 'start_time': start,
            'end_time': end, 'subjectcode': code, 'programcode': 'BSIT', 'yearlevel': 1,
            'sectionname': f'S{section}', 'roomname': 'PUP GYM'}


def _cross(monkeypatch, pol, published, new_rows, section_id, base=None):
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _Conn(_CrossCursor(published)))
    monkeypatch.setattr(database, 'load_scheduler_config', lambda: dict(base or GROUPS_BASE))
    monkeypatch.setattr(app_module, '_merge_policy_for',
                        lambda c, **k: (mg.GroupMergePolicy(pol.index, default_section_id=k.get('section_id'))
                                        if mg.merge_model(c) == 'groups' else None))
    viols, _ = app_module._check_cross_schedule_conflicts(new_rows, 'BSIT', 1, 'A', 'AY2627',
                                                          section_id=section_id)
    return viols


def test_cross_schedule_waives_only_the_same_event(monkeypatch):
    pol = policy(group(1, [1, 2, 3]))
    new = [dict(gene(2), section_id=None)]          # section comes from the context section
    assert _cross(monkeypatch, pol, [_published(1), _published(3)], new, 2) == []
    outsider = _cross(monkeypatch, pol, [_published(1), _published(9, code='GEED 001', fac='G')], new, 2)
    assert {v['rule'] for v in outsider} == {'HC11'}


def test_cross_schedule_reports_same_faculty_mismatch_with_another_member(monkeypatch):
    pol = policy(group(1, [1, 2], mode='SAME_FACULTY'))
    viols = _cross(monkeypatch, pol, [_published(1, fac='A')], [gene(2, fac='B')], 2)
    assert {(v['rule'], v.get('code')) for v in viols} >= {('HC16', 'HC16_FACULTY'), ('HC11', None)}


def test_cross_schedule_legacy_mode_unchanged(monkeypatch):
    # Legacy: NSTP, different faculty, other section, same room -> legacy merge, no conflict.
    viols = _cross(monkeypatch, policy(), [_published(1, fac='A')], [gene(2, fac='B')], 2, base=LEGACY)
    assert viols == []
    grouped = _cross(monkeypatch, policy(), [_published(1, fac='A')], [gene(2, fac='B')], 2)
    assert {v['rule'] for v in grouped} == {'HC11'}


# ── 28. Carried-forward Published (same section) ────────────────────────────────

def test_carried_forward_check_never_merges_within_a_section():
    submitted = [gene(1)]
    carried = [{'subjectcode': 'GEED 001', 'employeenumber': 'G', 'roomid': QUAD, 'daydesc': SUN,
                'days_list': [SUN], 'start_time': time(9, 0), 'end_time': time(12, 0)}]
    for base in (LEGACY, cfg(policy(group(1, [1, 2])))):
        out = app_module._publish_carried_forward_conflicts(submitted, carried, config=dict(base))
        assert {v['rule'] for v in out} == {'HC12'}


# ── 29. Request helpers ─────────────────────────────────────────────────────────

def test_requests_get_no_merge_exemption_in_group_mode():
    occupant = {'subjectcode': 'NSTP 001', 'employeenumber': 'A', 'programcode': 'BSIT', 'sectionname': 'S2'}
    legacy_req = {'subjectcode': 'NSTP 001', 'employeenumber': 'B', 'programcode': 'BSIT',
                  'sectionname': 'S1', '_cfg': dict(LEGACY)}
    assert app_module._request_occupant_blocks(legacy_req, occupant, 'room') is False   # legacy NSTP merge
    groups_req = dict(legacy_req, _cfg=dict(GROUPS_BASE))
    assert app_module._request_occupant_blocks(groups_req, occupant, 'room') is True
    assert app_module._request_occupant_blocks(dict(groups_req, employeenumber='A'), occupant, 'faculty') is True
    # a missing/TBA occupant faculty still never blocks
    assert app_module._request_occupant_blocks(groups_req, dict(occupant, employeenumber=None), 'faculty') is False


# ── Local check endpoint (backend wiring only; Local UX is P6) ──────────────────

class _LocalCursor:
    def __init__(self, official_rows):
        self.official_rows, self.last = official_rows, ''

    def execute(self, sql, params=None):
        self.last = sql

    def fetchone(self):
        if 'SELECT sectionname FROM public.sections' in self.last:
            return {'sectionname': 'S1'}
        if 'ORDER BY sv.version_number DESC' in self.last:
            return {'versionid': 1}
        return None

    def fetchall(self):
        return [dict(r) for r in self.official_rows] if "'Official' AS source" in self.last else []

    def close(self):
        pass

    def rollback(self):
        pass


def _local(monkeypatch, pol, base, other_section, other_code='NSTP 001', fac='F1', room=GYM):
    official = [{'source': 'Official', 'official_sessionid': 900, 'sectionid': other_section,
                 'sectionname': f'S{other_section}', 'programcode': 'BSIT', 'yearlevel': 1,
                 'subjectcode': other_code, 'daydesc': SUN, 'roomid': room, 'starttimeid': 4, 'endtimeid': 7,
                 'employeenumber': fac, 'start_fmt': '09:00 AM', 'end_fmt': '12:00 PM', 'instructor': 'x'}]
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _Conn(_LocalCursor(official)))
    monkeypatch.setattr(database, 'load_scheduler_config', lambda: dict(base))
    monkeypatch.setattr(app_module, 'query_db', lambda *a, **k: {'isactive': True})
    monkeypatch.setattr(app_module, '_merge_policy_for',
                        lambda c, **k: pol if mg.merge_model(c) == 'groups' else None)
    for name, value in {
        '_ensure_local_tables': lambda *a, **k: None,
        '_get_semester_id': lambda *a, **k: 1,
        '_validate_local_scope_binding': lambda *a, **k: True,
        '_validate_local_official_session_binding': lambda *a, **k: True,
        '_get_official_occurrence_faculty': lambda *a, **k: (fac, True),
        '_validate_local_session_structure': lambda *a, **k: (True, None),
        '_validate_local_editable_dimensions': lambda *a, **k: (True, None),
        '_validate_local_faculty_service_rules': lambda *a, **k: (True, None),
        '_local_upcoming_makeup_block': lambda *a, **k: None,
    }.items():
        monkeypatch.setattr(app_module, name, value)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(loggedin=True, role='Academic Head', username='p2-test')
    body = client.post('/api/local/check_room_conflicts', json={
        'ay_id': 'AY2627', 'term': 'A', 'program': 'BSIT', 'year_level': 1, 'section_id': 1,
        'sessions': [{'subject_code': 'NSTP 001', 'day': SUN, 'starttimeid': 4, 'endtimeid': 7,
                      'room_id': GYM, 'official_sessionid': 100}],
    }).get_json()
    assert body['success'] is True, body
    return sorted(c['conflict_type'] for c in body['conflicts'])


def _id_group(gid, sections):
    return group(gid, sections, meetings=[meeting(gid * 10 + 1, stid=4, etid=7)])


def test_local_check_uses_the_group_policy_in_group_mode(monkeypatch):
    pol = policy(_id_group(1, [1, 2]))
    assert _local(monkeypatch, pol, GROUPS_BASE, other_section=2) == []          # same event
    assert _local(monkeypatch, pol, GROUPS_BASE, other_section=9) == ['Faculty', 'Room']   # outsider


def test_local_check_legacy_mode_unchanged(monkeypatch):
    pol = policy()
    assert _local(monkeypatch, pol, LEGACY, other_section=9) == []               # legacy NSTP merge
    assert _local(monkeypatch, pol, GROUPS_BASE, other_section=9) == ['Faculty', 'Room']
