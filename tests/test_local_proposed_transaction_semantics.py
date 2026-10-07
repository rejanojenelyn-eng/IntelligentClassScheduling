"""Regression guards for coordinated Local proposal semantics (V5)."""
from constraints.context_conflicts import proposed_effective_occurrences


def test_three_way_chain_removes_all_replaced_pre_edit_occurrences():
    current = [
        {'official_sessionid': 1, 'subjectcode': 'A', 'daydesc': 'MONDAY'},
        {'official_sessionid': 2, 'subjectcode': 'B', 'daydesc': 'TUESDAY'},
        {'official_sessionid': 3, 'subjectcode': 'C', 'daydesc': 'WEDNESDAY'},
        {'official_sessionid': 4, 'subjectcode': 'D', 'daydesc': 'FRIDAY'},
    ]
    proposal = [
        {'official_sessionid': 1, 'subjectcode': 'A', 'daydesc': 'TUESDAY'},
        {'official_sessionid': 2, 'subjectcode': 'B', 'daydesc': 'WEDNESDAY'},
        {'official_sessionid': 3, 'subjectcode': 'C', 'daydesc': 'THURSDAY'},
    ]
    result = proposed_effective_occurrences(current, proposal)
    assert {(r['subjectcode'], r['daydesc']) for r in result} == {
        ('A', 'TUESDAY'), ('B', 'WEDNESDAY'), ('C', 'THURSDAY'), ('D', 'FRIDAY')
    }


def test_unaffected_occurrence_remains_in_proposed_effective_state():
    current = [
        {'official_sessionid': 1, 'subjectcode': 'A', 'daydesc': 'MONDAY'},
        {'official_sessionid': 9, 'subjectcode': 'X', 'daydesc': 'TUESDAY'},
    ]
    result = proposed_effective_occurrences(current, [
        {'official_sessionid': 1, 'subjectcode': 'A', 'daydesc': 'TUESDAY'}
    ])
    assert {(r['subjectcode'], r['daydesc']) for r in result} == {
        ('A', 'TUESDAY'), ('X', 'TUESDAY')
    }
