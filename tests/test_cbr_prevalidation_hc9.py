"""CBR prevalidation must not protect a historical faculty whose load would exceed HC9.

Bug fixed: HC9 load violations carry subject 'multiple', and prevalidate_cbr_assignment
kept only violations naming the gene's subject code, so an over-limit CBR faculty was
locked anyway (BSHM1 TMHM 002, AY2627 1st Sem: INVALID_RESULT in every seed).
"""
from scheduler import IntelligentScheduler


class _FakeCSP:
    def __init__(self, violations):
        self._violations = violations

    def validate(self, genes, faculty_map, existing_load=None):
        return list(self._violations)


def _scheduler(violations):
    s = IntelligentScheduler.__new__(IntelligentScheduler)
    s.csp = _FakeCSP(violations)
    return s


GENE = {'subject_code': 'TMHM 002', 'faculty_id': '10055'}


def test_hc9_of_the_genes_own_faculty_is_returned():
    v = {'rule': 'HC9', 'subject': 'multiple', 'faculty_id': '10055', 'detail': 'TS load 14.0 > 13.0'}
    assert _scheduler([v]).prevalidate_cbr_assignment(GENE, [], {}) == [v]


def test_hc9_of_another_faculty_is_ignored():
    v = {'rule': 'HC9', 'subject': 'multiple', 'faculty_id': '99999', 'detail': 'other faculty'}
    assert _scheduler([v]).prevalidate_cbr_assignment(GENE, [], {}) == []


def test_subject_violations_still_filtered_by_code():
    own = {'rule': 'HC1', 'subject': 'TMHM 002', 'detail': 'room conflict'}
    other = {'rule': 'HC1', 'subject': 'GEED 004', 'detail': 'room conflict'}
    assert _scheduler([own, other]).prevalidate_cbr_assignment(GENE, [], {}) == [own]


def test_hc9_maps_to_the_faculty_lock_only():
    assert IntelligentScheduler._CBR_RULE_TO_FLAG['HC9'] == 'faculty'
