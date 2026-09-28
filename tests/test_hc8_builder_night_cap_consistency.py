"""
Final HC8 builder/validator consistency (source-level regression guard).

HC8 (DESIGNEE_NIGHT_TEACHING_DAY_LIMIT) caps a designee at their
designation's PT/Night Teaching Service value = max DISTINCT days/week inside
the 6:00-9:00 PM Night Teaching Service window. _build_individual's proactive
avoidance and CSPValidator._check_night_pt_cap must read that SAME value via
the SAME shared helpers, never a separate formula or config key -- first the
retired "6 - nightteachingservice" formula and later the retired flat
hc7_max_night cap both let the builder and the validator disagree.
"""
import inspect

from scheduler import IntelligentScheduler, CSPValidator


def test_build_individual_night_cap_uses_no_retired_formula():
    src = inspect.getsource(IntelligentScheduler._build_individual)
    assert '6 - int(' not in src
    assert "get('hc7_max_night'" not in src
    assert '_night_cls_count' not in src   # row counting, not distinct days


def test_check_night_pt_cap_and_builder_share_the_same_rule_helpers():
    builder_src = inspect.getsource(IntelligentScheduler._build_individual)
    hc8_src = inspect.getsource(CSPValidator._check_night_pt_cap)
    assert 'designee_night_allowance' in builder_src and 'designee_night_allowance' in hc8_src
    assert 'is_night_service_slice' in builder_src
    assert 'collect_designee_night_days' in hc8_src
    assert "get('hc7_max_night'" not in hc8_src
