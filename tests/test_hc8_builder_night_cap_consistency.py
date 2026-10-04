"""
Phase B checkpoint 5: final HC audit finding.

_build_individual's proactive night-cap avoidance (used to decide when to
stop offering a designee evening blocks while building a candidate
schedule) was still computing the cap with the PRE-checkpoint-1 formula
("6 - nightteachingservice"), even though CSPValidator._check_night_pt_cap
(the authoritative final HC8 rule) has used a flat hc7_max_night config
value (default 2) since checkpoint 1 -- see
tests/test_characterization_hard_constraints.py for that characterization
and tests/test_phase_b_designee_pt.py for the paired final-policy proof.

The old formula (up to 6, depending on nightteachingservice) was up to 3x
looser than the real cap the schedule is actually validated against, so the
builder's own avoidance heuristic under-avoided overbooking a designee's
nights, leaving more HC8 violations than necessary for the GA's slower
mutate/repair/selection cycle to clean up instead of never creating them in
the first place. CSPValidator was never bypassed by this (a schedule that
slipped past the builder's heuristic was always still caught and corrected
downstream) -- this is a builder-efficiency fix, not a feasibility-safety
fix.

This is a source-level regression guard (same style as
test_cross_feature_consistency.py's centralized-route checks): it fails
loudly if the deprecated formula, or its stale "matches CSP validator"
comment, ever creeps back into _build_individual.
"""
import inspect

from scheduler import IntelligentScheduler


def test_build_individual_night_cap_no_longer_uses_deprecated_formula():
    src = inspect.getsource(IntelligentScheduler._build_individual)
    assert '6 - int(' not in src, (
        "_build_individual still computes the designee night cap as "
        "'6 - nightteachingservice' -- this was deprecated when hc7_max_night "
        "became the final HC8 policy in checkpoint 1. It must read "
        "self._hc_cfg.get('hc7_max_night', ...) instead, matching "
        "CSPValidator._check_night_pt_cap."
    )
    assert "hc7_max_night" in src, (
        "_build_individual's night-cap check must read the same hc7_max_night "
        "config key CSPValidator._check_night_pt_cap (final HC8) enforces."
    )


def test_check_night_pt_cap_and_builder_read_the_same_config_key():
    csp_src = inspect.getsource(IntelligentScheduler._build_individual)
    from scheduler import CSPValidator
    hc8_src = inspect.getsource(CSPValidator._check_night_pt_cap)
    assert "hc7_max_night" in csp_src and "hc7_max_night" in hc8_src
