"""Phase 3D: Requests/Local Arrangements retain specialized DB/date scopes
while exposing their shared conflict semantics through the frozen constraint
layer.  These tests intentionally do not require Flask or a database.
"""
from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "context_conflicts",
    Path(__file__).resolve().parents[1] / "constraints" / "context_conflicts.py",
)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def test_context_conflict_ids_use_frozen_inventory():
    assert cc.FACULTY_CONFLICT_RULE == 'HC10'
    assert cc.ROOM_CONFLICT_RULE == 'HC11'
    assert cc.SECTION_CONFLICT_RULE == 'HC12'
    assert cc.CROSS_SCHEDULE_RULE == 'HC15'
    assert cc.MERGE_VALIDITY_RULE == 'HC16'


def test_request_cohort_guard_is_not_mislabeled_as_hc12():
    rules = cc.request_conflict_rules(cohort_conflict=True)
    assert rules == [cc.REQUEST_COHORT_GUARD]
    assert 'HC12' not in rules


def test_request_shared_conflicts_are_mapped_to_hc_and_hc15():
    assert cc.request_conflict_rules(room_conflict=True) == ['HC11', 'HC15']
    assert cc.request_conflict_rules(faculty_conflict=True) == ['HC10', 'HC15']


def test_local_shared_conflicts_map_to_frozen_hc_semantics():
    assert cc.local_conflict_rules(section_conflict=True) == ['HC12', 'HC15']
    assert cc.local_conflict_rules(room_conflict=True, faculty_conflict=True) == [
        'HC11', 'HC15', 'HC10'
    ]


def test_app_preserves_specialized_sql_instead_of_replacing_with_csp():
    source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8-sig')
    request_block = source[source.index('def api_requests_validate'):source.index("@app.route('/api/requests/decide")]
    local_block = source[source.index('def api_local_check_room_conflicts'):source.index("@app.route('/api/get_room_schedule")]
    assert 'class_meeting_request' in request_block
    assert "sv.status = 'Published'" in request_block
    assert 'local_arrangement_sessions' in local_block
    assert "'constraint_rules'" in request_block
    assert "'constraint_rules'" in local_block
