"""Constraint semantics for date/database-backed feature conflict checks.

Requests and Local Arrangements cannot be replaced wholesale by CSPValidator:
they query persisted Published/Draft/request/local-arrangement occupancy and,
for make-up classes, a specific calendar date.  This module centralizes which
*frozen* constraint each shared conflict dimension represents while leaving
those feature-specific database scopes intact.

No SQL or scheduling policy is implemented here.
"""

FACULTY_CONFLICT_RULE = "HC10"
ROOM_CONFLICT_RULE = "HC11"
SECTION_CONFLICT_RULE = "HC12"
CROSS_SCHEDULE_RULE = "HC15"
MERGE_VALIDITY_RULE = "HC16"

# The Academic Head Requests endpoint historically checks an entire
# program+year-level cohort, not an exact section.  Preserve that behavior, but
# do not mislabel it as final HC12: it is a request-specific additional guard.
REQUEST_COHORT_GUARD = "REQUEST_COHORT_GUARD"


def request_conflict_rules(*, room_conflict=False, faculty_conflict=False,
                           cohort_conflict=False):
    """Return semantic rule metadata without changing request behavior."""
    rules = []
    if room_conflict:
        rules.extend([ROOM_CONFLICT_RULE, CROSS_SCHEDULE_RULE])
    if faculty_conflict:
        rules.extend([FACULTY_CONFLICT_RULE, CROSS_SCHEDULE_RULE])
    if cohort_conflict:
        rules.append(REQUEST_COHORT_GUARD)
    return list(dict.fromkeys(rules))


def local_conflict_rules(*, room_conflict=False, faculty_conflict=False,
                         section_conflict=False):
    """Return frozen HC metadata for Local Arrangement occupancy checks."""
    rules = []
    if room_conflict:
        rules.extend([ROOM_CONFLICT_RULE, CROSS_SCHEDULE_RULE])
    if faculty_conflict:
        rules.extend([FACULTY_CONFLICT_RULE, CROSS_SCHEDULE_RULE])
    if section_conflict:
        rules.extend([SECTION_CONFLICT_RULE, CROSS_SCHEDULE_RULE])
    return list(dict.fromkeys(rules))
