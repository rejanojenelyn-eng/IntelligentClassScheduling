"""HC15 effective/proposed-effective schedule semantics (dependency-free)."""
from constraints.context_conflicts import (
    interval_overlaps, effective_occurrences, proposed_effective_occurrences,
)


def test_exact_boundary_is_not_overlap_but_partial_overlap_is():
    assert interval_overlaps(6, 7.5, 7.5, 9) is False
    assert interval_overlaps(6, 8, 7.5, 9) is True


def test_published_local_replaces_only_its_official_occurrence():
    official = [
        {'official_sessionid': 101, 'daydesc': 'MONDAY', 'source': 'Official'},
        {'official_sessionid': 102, 'daydesc': 'TUESDAY', 'source': 'Official'},
    ]
    local = [
        {'official_sessionid': 102, 'daydesc': 'THURSDAY', 'source': 'Local'},
    ]
    result = effective_occurrences(official, local)
    assert [(r['official_sessionid'], r['daydesc']) for r in result] == [
        (101, 'MONDAY'), (102, 'THURSDAY')
    ]


def test_draft_does_not_vacate_official_slot_until_used_as_proposal():
    official = [{'official_sessionid': 201, 'daydesc': 'TUESDAY', 'source': 'Official'}]
    effective = effective_occurrences(official, [])
    assert effective[0]['daydesc'] == 'TUESDAY'
    proposed = proposed_effective_occurrences(effective, [
        {'official_sessionid': 201, 'daydesc': 'THURSDAY', 'source': 'Candidate Local'}
    ])
    assert len(proposed) == 1
    assert proposed[0]['daydesc'] == 'THURSDAY'


def test_coordinated_swap_is_evaluated_as_resulting_state():
    current = [
        {'official_sessionid': 1, 'faculty': 'A', 'daydesc': 'MONDAY'},
        {'official_sessionid': 2, 'faculty': 'B', 'daydesc': 'TUESDAY'},
    ]
    proposed = proposed_effective_occurrences(current, [
        {'official_sessionid': 1, 'faculty': 'A', 'daydesc': 'TUESDAY'},
        {'official_sessionid': 2, 'faculty': 'B', 'daydesc': 'MONDAY'},
    ])
    assert {(r['faculty'], r['daydesc']) for r in proposed} == {
        ('A', 'TUESDAY'), ('B', 'MONDAY')
    }


def test_valid_merge_exempts_faculty_and_room_but_not_same_section():
    from constraints.context_conflicts import overlapping_resource_conflicts
    a = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 1}
    b = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 1}
    assert overlapping_resource_conflicts(a, b, valid_merge=True) == ['Section']


def test_non_merge_reports_all_shared_exclusive_resources():
    from constraints.context_conflicts import overlapping_resource_conflicts
    a = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 1}
    b = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 1}
    assert overlapping_resource_conflicts(a, b, valid_merge=False) == ['Faculty', 'Room', 'Section']


def test_valid_merge_between_different_sections_exempts_shared_faculty_room():
    from constraints.context_conflicts import overlapping_resource_conflicts
    a = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 1}
    b = {'faculty_id': 'F1', 'room_id': 10, 'section_id': 2}
    assert overlapping_resource_conflicts(a, b, valid_merge=True) == []
