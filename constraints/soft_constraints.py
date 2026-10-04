"""
Soft-constraint adapter.

The final SC1-SC9 inventory comes from constraints.registry. Actual scoring
remains in scheduler.IntelligentScheduler._fitness during Phase 1, so this
module does not duplicate GA fitness arithmetic.
"""

from scheduler import IntelligentScheduler, CSPValidator
from .registry import SOFT_CONSTRAINT_IDS, get_soft_constraints

CURRENT_SOFT_CONSTRAINT_IDS = SOFT_CONSTRAINT_IDS


def definitions() -> dict:
    """Return the formal SC1-SC9 catalog."""
    return get_soft_constraints()


def score(individual: list, faculty_map: dict, config: dict = None,
          existing_load: dict = None, rooms_by_id: dict = None):
    """Run the current GA fitness scorer and return (score, hard_violation_count)."""
    sched = IntelligentScheduler()
    if config is not None:
        sched.csp = CSPValidator(config=config)
        sched._hc_cfg = config
    return sched._fitness(
        individual, faculty_map,
        existing_load=existing_load, rooms_by_id=rooms_by_id,
    )
