"""Phase 3C regression locks for Save Draft + Publish integration."""
import inspect
import app


def _src(name):
    return inspect.getsource(getattr(app, name))


def test_save_draft_uses_constraint_service_and_not_direct_csp():
    src = _src('api_save_draft')
    assert 'ConstraintService' in src
    assert 'CSPValidator(' not in src
    assert "v.get('severity') != 'warning'" in src


def test_publish_uses_constraint_service_and_warning_is_nonblocking():
    src = _src('api_approve_schedule')
    assert 'ConstraintService' in src
    assert 'CSPValidator(' not in src
    assert "v.get('severity') != 'warning'" in src


def test_publish_retains_server_side_completeness_gate():
    src = _src('api_approve_schedule')
    assert 'sv.is_incomplete = TRUE' in src
    assert 'Cannot approve: this Draft has unresolved required components' in src


def test_publish_retains_hc15_cross_schedule_gate_and_hc16_merge_adapter():
    src = _src('api_approve_schedule')
    assert 'HC15 cross-schedule validation' in src
    assert 'hard_constraints as _hc_adapter' in src
    assert '_hc_adapter.is_valid_merge' in src


def test_save_draft_comment_uses_final_hc7_number_for_day_pairing():
    src = _src('api_save_draft')
    assert 'HC7 (Required Day Pairing)' in src
    assert 'HC6 is Standard' in src
