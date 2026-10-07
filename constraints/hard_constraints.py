"""
Hard-constraint adapter.

The final HC1-HC17 inventory comes from constraints.registry. Actual enforcement
remains in scheduler.CSPValidator during Phase 1, so existing scheduling
behavior is unchanged.
"""

from scheduler import CSPValidator, resolve_legacy_rule_id, LEGACY_RULE_ID_MAP
from .registry import HARD_CONSTRAINT_IDS, get_hard_constraints

CURRENT_HARD_CONSTRAINT_IDS = HARD_CONSTRAINT_IDS

# Rules CSPValidator can emit directly as final HC violations. HC15 is still
# distributed; HC16 is a decision primitive; HC17 is folded into HC9. HC14 is
# listed because its check exists, but it is INACTIVE in practice: no
# class-size/enrollment data exists, so it never emits today. Former HC_SPEC is
# intentionally not a final hard constraint.
VIOLATION_EMITTING_IDS = (
    "HC1", "HC2", "HC3", "HC4", "HC5", "HC6", "HC7",
    "HC8", "HC9", "HC10", "HC11", "HC12", "HC13", "HC14",
)


def definitions() -> dict:
    """Return the formal HC1-HC17 catalog."""
    return get_hard_constraints()


def validate(schedule: list, faculty_map: dict, config: dict = None,
             skip_rules: set = None, existing_load: dict = None,
             rooms_by_id: dict = None) -> list:
    """Pass through to the current authoritative CSPValidator."""
    return CSPValidator(config=config).validate(
        schedule, faculty_map, skip_rules=skip_rules,
        existing_load=existing_load, rooms_by_id=rooms_by_id,
    )


def is_valid_merge(a: dict, b: dict, config: dict = None) -> bool:
    """HC16 decision primitive; pass through to CSPValidator.is_valid_merge."""
    return CSPValidator(config=config).is_valid_merge(a, b)


def times_overlap(start1, end1, start2, end2) -> bool:
    """Shared interval-overlap primitive used by the existing CSP checks."""
    return CSPValidator._times_overlap(start1, end1, start2, end2)
