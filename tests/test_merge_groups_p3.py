"""HC17 under the group model — P3 unified merged-class faculty load (no database).

P7: with NO HC17 policy mapped to a merged class, every section's row keeps its full
hours and units (per-section default — merging shares the room and slot, not the
load); only an explicitly mapped policy (e.g. NSTP) credits the shared class once.

HC16 (MergeIndex) decides WHICH rows are one merged event; HC17
(GroupMergePolicy.credit_rows) only decides how that event counts toward each
faculty's load. Every load path (group_assignments / compute_load_buckets /
summarize / merge_aware_additional_hours / HC9 / SC4 / display / portal) runs on
that one calculation. Legacy mode is unchanged.
"""
import os
from datetime import time

import pytest

import app as app_module
import faculty_load as fl
import merge_groups as mg
from scheduler import CSPValidator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY = {'hc_merge_enabled': 1, 'hc_merge_scope': 'nstp_only', 'hc_merge_section_pairs': '[]'}
GYM, QUAD = 10, 11


def meeting(mid, day='Sunday', start='09:00', end='12:00', room=GYM):
    return {'mergegroupmeetingid': mid, 'class_type': 'Lecture', 'daydesc': day, 'start': start, 'end': end,
            'roomid': room, 'roomname': 'GYM'}


def group(gid, sections, *, code='NSTP 001', meetings=None, mode='MULTIPLE_FACULTY', designated=None,
          active=True, teaching=3.0, units=3.0):
    meetings = [meeting(gid * 10 + 1)] if meetings is None else meetings
    return {'mergegroupid': gid, 'groupname': f'Group {gid}', 'is_active': active, 'config_errors': [],
            'faculty_mode': mode, 'employeenumber': designated,
            'members': [{'sectionid': s, 'subjectcode': code} for s in sections],
            'meetings': meetings, 'state': 'SCHEDULED',
            'completeness': {'complete_types': ['Lecture'], 'required': {'Lecture': 3.0}},
            'ref_teachinghours': teaching, 'ref_creditunits': units}


def rule(pid, lo, hi, name='Shared Load'):
    return {'policyid': pid, 'policyname': name, 'subjectcode': 'NSTP', 'min_sections': lo, 'max_sections': hi}


def policy(*groups, rules=None):
    return mg.GroupMergePolicy(mg.MergeIndex(list(groups)), load_rules=rules or {})


def cfg(pol):
    return mg.with_policy(dict(LEGACY, hc_merge_model='groups'), pol)


def row(section, *, fac='F1', code='NSTP 001', day='Sunday', start='09:00', end='12:00', room=GYM, units=3):
    sh, eh = int(start[:2]), int(end[:2])
    return {'employeenumber': fac, 'sectionid': section, 'subjectcode': code, 'days': day,
            'start': start, 'end': end, 'roomid': room, 'room': 'GYM',
            'hrs': (int(end[:2]) * 60 + int(end[3:]) - int(start[:2]) * 60 - int(start[3:])) / 60,
            'time_code': f'{sh:02d}{eh:02d}', 'time_range': f'{start} - {end}',
            'year_section': f'SEC-{section}', 'units': units, 'subjectname': 'NSTP 1'}


def total(meta):
    return round(sum(m['credit'] for m in meta), 2)


# ── 1-4. Per section by default; once only under a mapped policy ────────────────

@pytest.mark.parametrize('n', [2, 3, 5])
def test_same_event_without_a_policy_counts_per_section(n):
    secs = list(range(1, n + 1))
    meta = policy(group(1, secs)).credit_rows([row(s) for s in secs])
    assert total(meta) == 3.0 * n
    assert all(m['merged_group'] is None and m['rule'] is None for m in meta)


@pytest.mark.parametrize('n', [2, 3, 5])
def test_same_event_same_faculty_counts_once_under_a_mapped_policy(n):
    secs = list(range(1, n + 1))
    meta = policy(group(1, secs), rules={1: [rule(7, 2, 5)]}).credit_rows([row(s) for s in secs])
    assert total(meta) == 3.0
    assert sum(m['keeper'] for m in meta) == 1 and all(m['section_count'] == n for m in meta)


def test_multiple_faculty_event_gives_each_faculty_their_own_load():
    pol = policy(group(1, [1, 2, 3]), rules={1: [rule(7, 1, 5)]})
    meta = pol.credit_rows([row(1, fac='X'), row(2, fac='Y'), row(3, fac='Z')])
    assert [m['credit'] for m in meta] == [3.0, 3.0, 3.0]
    assert all(m['section_count'] == 1 for m in meta)            # sections handled by that faculty


# ── 5-6, 29-30. No event identity -> no collapsing (no inference, no prefix) ────

def test_same_subject_day_time_without_a_group_is_not_collapsed():
    assert total(policy().credit_rows([row(1), row(2), row(3)])) == 9.0


def test_same_subject_in_different_groups_is_not_collapsed():
    pol = policy(group(1, [1, 2]), group(2, [3, 4], meetings=[meeting(21)]))
    assert total(pol.credit_rows([row(1), row(3)])) == 6.0


@pytest.mark.parametrize('code', ['NSTP 001', 'OU 101', 'GEED 001'])
def test_group_mode_never_collapses_by_subject_prefix(code):
    rows = [row(1, code=code), row(2, code=code)]
    assert total(policy().credit_rows(rows)) == 6.0
    grouped = fl.group_assignments(rows, config=cfg(policy()))
    assert round(sum(g['hrs'] for g in grouped), 2) == 6.0


# ── 7-8. Multiple meetings: hours per event, units once ─────────────────────────

def test_two_meetings_of_one_group_are_two_events_counted_once_each():
    mts = [meeting(11, day='Monday', start='09:00', end='10:30'),
           meeting(12, day='Thursday', start='09:00', end='10:30')]
    pol = policy(group(1, [1, 2], meetings=mts), rules={1: [rule(7, 2, 2)]})
    rows = [row(s, day=d, start='09:00', end='10:30') for s in (1, 2) for d in ('Monday', 'Thursday')]
    assert total(pol.credit_rows(rows)) == 3.0                    # 1.5h Monday + 1.5h Thursday
    assert total(policy(group(1, [1, 2], meetings=mts)).credit_rows(rows)) == 6.0   # no policy: per section


def test_units_are_credited_once_however_many_weekly_meetings():
    mts = [meeting(11, day='Monday', start='09:00', end='10:30'),
           meeting(12, day='Thursday', start='09:00', end='10:30')]
    pol = policy(group(1, [1, 2], meetings=mts), rules={1: [rule(7, 2, 2)]})
    rows = [row(s, day=d, start='09:00', end='10:30') for s in (1, 2) for d in ('Monday', 'Thursday')]
    grouped = fl.group_assignments(rows, config=cfg(pol))
    assert len(grouped) == 1 and grouped[0]['units'] == 3 and round(grouped[0]['hrs'], 2) == 3.0
    assert grouped[0]['days'] == 'Monday, Thursday'
    # a normal (non-merged) multi-slice subject keeps one entry, units once
    normal = fl.group_assignments([row(7, code='IT 101', day='Monday', start='09:00', end='10:30'),
                                   row(7, code='IT 101', day='Thursday', start='09:00', end='10:30')],
                                  config=cfg(pol))
    assert len(normal) == 1 and normal[0]['units'] == 3


# ── 9-16. HC17 policies: explicit group mapping and section ranges ──────────────

def _two_hour_group(**kw):
    return group(1, [1, 2, 3, 4], meetings=[meeting(11, start='09:00', end='11:00')], teaching=3.0, units=1.0, **kw)


def _rows(n):
    return [row(s, start='09:00', end='11:00') for s in range(1, n + 1)]


def test_matching_policy_credits_curriculum_hours_and_units_once():
    pol = policy(_two_hour_group(), rules={1: [rule(7, 2, 3)]})
    meta = pol.credit_rows(_rows(3))
    assert total(meta) == 3.0                                    # curriculum teaching hours, not 2h
    assert {m['rule']['policyid'] for m in meta} == {7} and {m['units'] for m in meta} == {1.0}
    grouped = fl.group_assignments(_rows(3), config=cfg(pol))
    assert grouped[0]['units'] == 1.0 and grouped[0]['merge_policy'] == 'Shared Load'


def test_no_matching_policy_counts_every_section_in_full():
    pol = policy(_two_hour_group())                             # group with no mapped policy
    meta = pol.credit_rows(_rows(3))
    assert total(meta) == 6.0 and all(m['rule'] is None and m['units'] is None for m in meta)
    # display: one ordinary entry per section, each with the subject's own units
    grouped = fl.group_assignments([dict(r, units=1) for r in _rows(3)], config=cfg(pol))
    assert len(grouped) == 3 and [g['units'] for g in grouped] == [1, 1, 1]


@pytest.mark.parametrize('n,expected', [(2, 4.0), (3, 3.0), (4, 3.0)], ids=['below', 'min', 'max'])
def test_section_count_boundaries(n, expected):
    pol = policy(_two_hour_group(), rules={1: [rule(7, 3, 4)]})
    assert total(pol.credit_rows(_rows(n))) == expected


def test_section_count_above_range_falls_back():
    pol = policy(group(1, [1, 2, 3, 4, 5], meetings=[meeting(11, start='09:00', end='11:00')]),
                 rules={1: [rule(7, 2, 3)]})
    assert total(pol.credit_rows(_rows(5))) == 10.0              # out of range: per section


def test_ambiguous_overlapping_ranges_are_detected():
    existing = {1: [rule(8, 3, 5)]}
    assert mg.policy_mapping_conflicts(rule(7, 2, 4), [1], existing)
    assert mg.policy_mapping_conflicts(rule(7, 2, 3), [1], {1: [rule(8, 3, 4)]})      # shared boundary
    assert mg.policy_mapping_conflicts(rule(7, 2, 3), [1], {1: [rule(8, 4, 5)]}) == []
    assert mg.policy_mapping_conflicts(rule(7, 2, 4), [1], {1: [rule(7, 2, 4)]}) == []  # itself


def test_one_policy_may_apply_to_many_groups():
    g1 = _two_hour_group()
    g2 = dict(group(2, [5, 6], meetings=[meeting(21, day='Saturday', start='09:00', end='11:00')],
                    teaching=3.0), mergegroupid=2)
    pol = policy(g1, g2, rules={1: [rule(7, 2, 4)], 2: [rule(7, 2, 4)]})
    rows = _rows(2) + [row(s, day='Saturday', start='09:00', end='11:00') for s in (5, 6)]
    assert total(pol.credit_rows(rows)) == 6.0                  # 3h per group, each credited once


def test_policy_applicability_requires_explicit_group_mapping():
    # An NSTP subject-coded policy that is NOT mapped to this group never applies.
    pol = policy(_two_hour_group(), rules={99: [rule(7, 2, 4)]})
    assert total(pol.credit_rows(_rows(3))) == 6.0


# ── 17-19, 31. Invalid / inactive / TBA: HC17 never creates a merge ─────────────

def test_inactive_group_is_normal_load():
    pol = policy(group(1, [1, 2], active=False))
    assert total(pol.credit_rows([row(1), row(2)])) == 6.0


def test_invalid_event_is_normal_load_even_with_a_policy():
    pol = policy(group(1, [1, 2]), rules={1: [rule(7, 2, 3)]})
    meta = pol.credit_rows([row(1), row(2, room=QUAD)])           # section 2 off the group's room
    assert total(meta) == 6.0
    assert meta[1]['merged_group'] is None                       # the invalid occurrence is normal load
    assert all(m['rule'] is None for m in meta)                  # one section left: no policy range met


def test_designated_faculty_mismatch_is_not_merged_load():
    pol = policy(group(1, [1, 2], mode='SAME_FACULTY', designated='A'))
    assert total(pol.credit_rows([row(1, fac='B'), row(2, fac='B')])) == 6.0


def test_tba_rows_never_become_merged_load():
    meta = policy(group(1, [1, 2])).credit_rows([row(1, fac=None), row(2, fac='TBA')])
    assert all(m['merged_group'] is None for m in meta)


def test_missing_policy_never_invalidates_the_merge():
    pol = policy(group(1, [1, 2]))
    meta = pol.credit_rows([row(1), row(2)])
    assert total(meta) == 6.0                                    # load per section ...
    occ = dict(subject_code='NSTP 001', day='Sunday', start_time='09:00', end_time='12:00', room_id=GYM)
    assert pol.same_event(dict(occ, sectionid=1), dict(occ, sectionid=2))   # ... still one merged class


# ── 20, 23-25. Every load path agrees ───────────────────────────────────────────

FAC = {'F1': {'fullname': 'F One', 'employeestatus': 'Permanent',
              'employeetype': {'regularload': 0, 'parttimeload': 3, 'teachingsubstitution': 0}}}


def test_compute_load_buckets_and_group_assignments_count_the_event_once():
    pol = policy(group(1, [1, 2, 3]), rules={1: [rule(7, 2, 3)]})
    rows = [row(s) for s in (1, 2, 3)]
    grouped = fl.group_assignments(rows, config=cfg(pol))
    assert len(grouped) == 1
    assert grouped[0]['year_section'] == 'SEC-1, SEC-2, SEC-3' and grouped[0]['merge_group'] == 'Group 1'
    buckets = fl.compute_load_buckets(rows, {'typename': 'Permanent', 'regularload': 30, 'parttimeload': 12},
                                      config=cfg(pol))
    assert buckets['used'] == 3.0


def test_merge_aware_additional_hours_counts_marginal_credit():
    pol = policy(_two_hour_group(), rules={1: [rule(7, 2, 4)]})
    existing = [dict(r, faculty_id='F1') for r in _rows(2)]       # 2 sections: policy, 3h once
    new = [{'faculty_id': 'F1', 'subject_code': 'NSTP 001', 'section_id': 3, 'days_list': ['Sunday'],
            'start_time': time(9, 0), 'end_time': time(11, 0), 'room_id': GYM}]
    # third section joins the same shared event: still the policy's 3h, nothing more
    assert fl.merge_aware_additional_hours(new, existing, config=cfg(pol)) == 0.0
    # without a policy every section counts: the third adds its full 2h
    assert fl.merge_aware_additional_hours(new, existing, config=cfg(policy(_two_hour_group()))) == 2.0
    other = [dict(new[0], subject_code='IT 101', section_id=9)]
    assert fl.merge_aware_additional_hours(other, existing, config=cfg(pol)) == 2.0


def test_hc9_enforces_exactly_the_displayed_merged_load():
    pol = policy(_two_hour_group(), rules={1: [rule(7, 3, 4)]})
    existing_rows = [dict(r, employeenumber='F1') for r in _rows(2)]
    summary = fl.summarize_faculty_load(existing_rows, config=cfg(pol))
    gene = {'subject_code': 'NSTP 001', 'faculty_id': 'F1', 'room_id': GYM, 'section_id': 3, 'day': 'Sunday',
            'days_list': ['Sunday'], 'start_time': time(9, 0), 'end_time': time(11, 0), 'duration_hrs': 2.0}
    displayed = round(sum(g['hrs'] for g in fl.group_assignments(existing_rows + [dict(row(3, start='09:00', end='11:00'))],
                                                                   config=cfg(pol))), 2)
    assert displayed == 3.0

    def hc9(cap):
        fac = {'F1': {'fullname': 'F One', 'employeestatus': 'Permanent',
                      'employeetype': {'regularload': 0, 'parttimeload': cap, 'teachingsubstitution': 0}}}
        csp = CSPValidator(config=cfg(mg.GroupMergePolicy(pol.index, default_section_id=3, load_rules=pol.load_rules)))
        return [v for v in csp._check_load_limits([gene], fac, existing_load={'F1': summary}) if v['rule'] == 'HC9']

    assert hc9(3.0) == []                 # exactly the displayed 3h fits
    assert hc9(2.9)                        # one bit less does not


# ── 26. SC4 uses the shared credit in group mode ─────────────────────────────────

def test_sc4_group_branch_uses_the_shared_credit():
    src = open(os.path.join(ROOT, 'scheduler.py'), encoding='utf-8').read()
    sc4 = src[src.index('# SC4 — Balance Faculty/Part-Time Load'):src.index('return score, len(hard_violations)')]
    assert 'self.csp._merge_policy.credit_rows' in sc4


# ── 27. Cross-program load check ─────────────────────────────────────────────────

class _Cur:
    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return {'academicyearid': 'AY2627', 'semestertype': 'A'}

    def close(self):
        pass


class _Conn:
    def cursor(self, **k):
        return _Cur()

    def close(self):
        pass


def test_cross_program_check_credits_the_merged_event_once(monkeypatch):
    pol = mg.GroupMergePolicy(policy(group(1, [1, 2])).index, default_section_id=2,
                              load_rules={1: [rule(7, 2, 2)]})
    existing_rows = [row(1)]
    summary = fl.summarize_faculty_load(existing_rows, config=cfg(pol))
    monkeypatch.setattr(app_module, 'get_db_connection', lambda: _Conn())
    monkeypatch.setattr(app_module, '_load_cfg_for', lambda *a, **k: cfg(pol))
    monkeypatch.setattr(app_module.faculty_load, 'get_faculty_load_batch', lambda *a, **k: {'F1': summary})
    fac = {'F1': {'fullname': 'F One', 'employeetype': {'regularload': 0, 'parttimeload': 3,
                                                        'teachingsubstitution': 0}}}
    same_event = [{'faculty_id': 'F1', 'subject_code': 'NSTP 001', 'days_list': ['Sunday'],
                   'start_time': '09:00', 'end_time': '12:00', 'room_id': GYM}]
    assert app_module._check_cross_program_faculty_loads(same_event, fac, 1, exclude_section_id=2) == []
    elsewhere = [dict(same_event[0], room_id=QUAD)]                 # not the event -> 3h more
    assert app_module._check_cross_program_faculty_loads(elsewhere, fac, 1, exclude_section_id=2)


# ── 28. Legacy HC17 unchanged ────────────────────────────────────────────────────

def test_legacy_group_assignments_and_buckets_are_unchanged():
    rows = [row(1), row(2)]
    legacy = fl.group_assignments(rows, config=dict(LEGACY))
    assert [g['hrs'] for g in legacy] == [3.0, 0.0]           # legacy NSTP inference collapses
    # a policy object in a legacy config is ignored
    assert fl.group_assignments(rows, config=mg.with_policy(dict(LEGACY), policy())) == legacy
    assert fl.credited_session_hours(rows, None) == [3.0, 3.0]
    assert fl.merge_aware_additional_hours(
        [{'faculty_id': 'F1', 'subject_code': 'NSTP 001', 'days_list': ['Sunday'],
          'start_time': time(9, 0), 'end_time': time(12, 0)}], [], config=dict(LEGACY)) == 3.0


def test_display_collapse_uses_the_same_credit():
    pol = policy(_two_hour_group(), rules={1: [rule(7, 3, 4)]})
    shown = pol.collapse_for_display(_rows(3))
    assert len(shown) == 1 and shown[0]['hrs'] == 3.0 and shown[0]['units'] == 1.0
    assert shown[0]['year_section'] == 'SEC-1, SEC-2, SEC-3' and shown[0]['merged_sections'] == 3
