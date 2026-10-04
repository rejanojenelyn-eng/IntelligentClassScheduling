"""Phase 3A — Generation / frozen-constraint integration guards.

These tests intentionally do not move GA/CSP implementation out of scheduler.py.
IntelligentScheduler is the algorithm engine; ConstraintService is the public
feature-facing validation/scoring adapter. Moving generate_draft() behind the
service would create a circular dependency without improving rule ownership.
"""

import inspect

from constraints import (
    ConstraintService,
    HARD_CONSTRAINT_IDS,
    SOFT_CONSTRAINT_IDS,
)
from constraints.hard_constraints import CURRENT_HARD_CONSTRAINT_IDS
from constraints.soft_constraints import CURRENT_SOFT_CONSTRAINT_IDS
from scheduler import IntelligentScheduler, CSPValidator


def test_generation_uses_the_authoritative_csp_engine():
    """The GA engine must retain one CSPValidator instance as its hard-rule authority."""
    source = inspect.getsource(IntelligentScheduler.generate_draft)
    assert "self.csp = CSPValidator(config=self._hc_cfg)" in source


def test_frozen_constraint_inventory_is_exact():
    assert HARD_CONSTRAINT_IDS == tuple(f"HC{i}" for i in range(1, 18))
    assert SOFT_CONSTRAINT_IDS == tuple(f"SC{i}" for i in range(1, 10))
    assert CURRENT_HARD_CONSTRAINT_IDS == HARD_CONSTRAINT_IDS
    assert CURRENT_SOFT_CONSTRAINT_IDS == SOFT_CONSTRAINT_IDS
    assert "HC_SPEC" not in HARD_CONSTRAINT_IDS


def test_constraint_service_delegates_to_generation_engines():
    """Guard the intended boundary: service -> existing CSP / GA fitness engines."""
    hard_source = inspect.getsource(ConstraintService.validate_schedule)
    soft_source = inspect.getsource(ConstraintService.score_soft_constraints)
    assert "_hard.validate" in hard_source
    assert "_soft.score" in soft_source


def test_hc_spec_is_compatibility_only_not_final_hard_inventory():
    assert "HC_SPEC" not in CURRENT_HARD_CONSTRAINT_IDS
    # The legacy identifier may still be understood by CSPValidator for advisory
    # warnings; it must not become an 18th final hard constraint.
    assert hasattr(CSPValidator, "_check_faculty_specialization")
