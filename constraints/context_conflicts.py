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


def interval_overlaps(start_a, end_a, start_b, end_b):
    """Half-open interval overlap used by HC10/HC11/HC12.

    Exact end-to-start boundaries are allowed: 08:00-09:00 and 09:00-10:00
    do not overlap. Inputs only need to support normal ``<``/``>`` ordering.
    """
    return start_a < end_b and end_a > start_b


def effective_occurrences(official_rows, published_local_rows):
    """Return the HC15 effective schedule from already-fetched rows.

    Contract: Published Official minus Official occurrences superseded by an
    active Published Local row, plus those Published Local rows. Draft Local
    rows must not be supplied here because drafts do not reserve resources.
    The helper is intentionally database-agnostic so every feature can share
    the same state semantics while retaining its own SQL scope.
    """
    local = list(published_local_rows or [])
    overridden = {
        row.get('official_sessionid') for row in local
        if row.get('official_sessionid') is not None
    }
    official = [
        row for row in (official_rows or [])
        if row.get('official_sessionid', row.get('sessionid')) not in overridden
    ]
    return official + local


def proposed_effective_occurrences(current_effective_rows, proposed_rows):
    """Overlay one Local transaction on the current HC15 effective schedule.

    Every proposed row replaces the effective occurrence with the same
    ``official_sessionid``. This makes coordinated moves/swaps validate against
    their resulting state rather than against stale pre-edit occupancy.
    """
    proposed = list(proposed_rows or [])
    replaced = {
        row.get('official_sessionid') for row in proposed
        if row.get('official_sessionid') is not None
    }
    kept = [
        row for row in (current_effective_rows or [])
        if row.get('official_sessionid') not in replaced
    ]
    return kept + proposed


def _assigned_faculty(faculty_id):
    # Kept local (this module stays dependency-free); same rule as
    # faculty_load.has_assigned_faculty: a missing/TBA instructor never conflicts.
    return bool(faculty_id) and str(faculty_id).strip().upper() not in ('TBA', 'NONE', 'NULL')


def _resource_id(value):
    """Normalize a faculty/room/section id for equality checks: '18' and 18 match;
    blank and TBA mean "no resource" (never conflicts)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in ('TBA', 'NONE', 'NULL'):
        return None
    return text


def overlapping_resource_conflicts(a, b, *, valid_merge=False, faculty_exempt=None):
    """Return HC10/HC11/HC12 resource dimensions shared by two occurrences.

    ``a``/``b`` are normalized occurrence dictionaries.  Room overlap is exempt
    when HC16 has already established that the pair is one valid merged/shared
    class.  Faculty overlap is exempt per the shared HC10 rule
    (faculty_load.faculty_overlap_exempt: valid HC16 merge OR the NSTP/OU
    shared-faculty exemption); callers pass that decision as ``faculty_exempt``
    (defaults to ``valid_merge``).  A missing/TBA faculty never conflicts.
    Section overlap is never exempted merely by HC16: one section still cannot
    attend two independent occurrence rows at once.  Callers should invoke this
    only after day/time overlap is known.
    """
    if faculty_exempt is None:
        faculty_exempt = valid_merge
    conflicts = []
    # Ids are compared as text: the editor sends room/section ids as strings ('18')
    # while DB rows carry ints (18), and '18' == 18 is False — that silently let a
    # room double-booking through the cross-schedule check.
    fac_a, fac_b = _resource_id(a.get('faculty_id')), _resource_id(b.get('faculty_id'))
    room_a, room_b = _resource_id(a.get('room_id')), _resource_id(b.get('room_id'))
    sec_a, sec_b = _resource_id(a.get('section_id')), _resource_id(b.get('section_id'))
    same_faculty = _assigned_faculty(fac_a) and fac_a == fac_b
    same_room = room_a is not None and room_a == room_b
    same_section = sec_a is not None and sec_a == sec_b
    if same_faculty and not faculty_exempt:
        conflicts.append('Faculty')
    if same_room and not valid_merge:
        conflicts.append('Room')
    if same_section:
        conflicts.append('Section')
    return conflicts
