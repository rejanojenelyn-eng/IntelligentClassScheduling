"""
constraints/ -- centralized scheduling-constraint service.

Phase 1 keeps the existing scheduler.CSPValidator and
scheduler.IntelligentScheduler._fitness as the working enforcement/scoring
engines while this package provides the stable public service and the formal
HC1-HC17 / SC1-SC9 catalog.

Frozen integration boundary:
    Feature validation/scoring -> ConstraintService -> existing CSPValidator / _fitness
    Schedule generation        -> IntelligentScheduler -> CSPValidator / _fitness

Generation deliberately remains inside IntelligentScheduler because it owns the
CBR -> CSP -> GA algorithm itself. ConstraintService is the stable public entry
point for feature-level validation/scoring; it delegates back to those proven
engines rather than duplicating them. This avoids a circular dependency while
keeping HC1-HC17 and SC1-SC9 formally cataloged in registry.py.
"""

from .constraint_service import ConstraintService
from .models import ConstraintViolation, ValidationResult, ScheduleContext
from .policy import SchedulingPolicy
from . import context_conflicts
from .registry import (
    CONSTRAINTS,
    HARD_CONSTRAINTS,
    SOFT_CONSTRAINTS,
    HARD_CONSTRAINT_IDS,
    SOFT_CONSTRAINT_IDS,
    ALL_CONSTRAINT_IDS,
    get_constraint,
    get_hard_constraints,
    get_soft_constraints,
    get_all_constraints,
)

__all__ = [
    "ConstraintService", "ConstraintViolation", "ValidationResult",
    "ScheduleContext", "SchedulingPolicy",
    "CONSTRAINTS", "HARD_CONSTRAINTS", "SOFT_CONSTRAINTS",
    "HARD_CONSTRAINT_IDS", "SOFT_CONSTRAINT_IDS", "ALL_CONSTRAINT_IDS",
    "get_constraint", "get_hard_constraints", "get_soft_constraints",
    "get_all_constraints", "context_conflicts",
]
