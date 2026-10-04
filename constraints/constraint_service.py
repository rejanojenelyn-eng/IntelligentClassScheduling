"""
ConstraintService -- stable public backend entry point for scheduling
constraint validation/scoring.

Phase 1 centralizes formal HC1-HC17 and SC1-SC9 definitions in registry.py
without rewriting the proven scheduler algorithms. Hard validation delegates to
scheduler.CSPValidator; soft scoring delegates to
scheduler.IntelligentScheduler._fitness.
"""

from .models import ValidationResult
from .policy import SchedulingPolicy
from . import hard_constraints as _hard
from . import soft_constraints as _soft
from .registry import (
    get_constraint,
    get_hard_constraints,
    get_soft_constraints,
    get_all_constraints,
)


class ConstraintService:
    def __init__(self, policy: SchedulingPolicy = None):
        self.policy = policy or SchedulingPolicy.load()

    @classmethod
    def with_current_policy(cls) -> "ConstraintService":
        return cls(SchedulingPolicy.load())

    def validate_assignment(self, assignment: dict, context_schedule: list,
                            faculty_map: dict, existing_load: dict = None,
                            rooms_by_id: dict = None) -> ValidationResult:
        """Validate context_schedule + assignment through the existing full CSP pass."""
        full_schedule = list(context_schedule) + [assignment]
        violations = _hard.validate(
            full_schedule, faculty_map, config=self.policy.as_dict(),
            existing_load=existing_load, rooms_by_id=rooms_by_id,
        )
        return ValidationResult(violations=violations)

    def validate_schedule(self, schedule: list, faculty_map: dict,
                          skip_rules: set = None, existing_load: dict = None,
                          rooms_by_id: dict = None) -> ValidationResult:
        """Validate a complete schedule through the authoritative CSPValidator."""
        violations = _hard.validate(
            schedule, faculty_map, config=self.policy.as_dict(),
            skip_rules=skip_rules, existing_load=existing_load,
            rooms_by_id=rooms_by_id,
        )
        return ValidationResult(violations=violations)

    def score_soft_constraints(self, individual: list, faculty_map: dict,
                               existing_load: dict = None,
                               rooms_by_id: dict = None):
        """Return _fitness()'s existing (score, hard_violation_count) pair."""
        return _soft.score(
            individual, faculty_map, config=self.policy.as_dict(),
            existing_load=existing_load, rooms_by_id=rooms_by_id,
        )

    def get_constraint(self, rule_id: str):
        """Return the formal definition for one HC/SC ID, or None."""
        return get_constraint(rule_id)

    def get_hard_constraint_details(self) -> dict:
        return get_hard_constraints()

    def get_soft_constraint_details(self) -> dict:
        return get_soft_constraints()

    def get_constraint_details(self) -> dict:
        """Return the complete formal catalog plus policy-maintenance metadata."""
        return {
            "hard_constraints": get_hard_constraints(),
            "soft_constraints": get_soft_constraints(),
            "all_constraints": get_all_constraints(),
            # Compatibility fields retained for existing tooling.
            "hard_constraint_ids": list(_hard.CURRENT_HARD_CONSTRAINT_IDS),
            "soft_constraint_ids": list(_soft.CURRENT_SOFT_CONSTRAINT_IDS),
            "dead_config_keys": list(SchedulingPolicy.DEAD_KEYS),
            "write_only_config_keys": list(SchedulingPolicy.WRITE_ONLY_KEYS),
        }
