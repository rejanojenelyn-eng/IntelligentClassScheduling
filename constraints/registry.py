"""
Central constraint registry for the scheduling DSS.

This module is the authoritative CATALOG / DEFINITION source for the final
HC1-HC17 and SC1-SC9 specification. It deliberately does not duplicate the
working algorithms. Enforcement remains in scheduler.CSPValidator and soft
scoring remains in scheduler.IntelligentScheduler._fitness during Phase 1.

The purpose of this registry is to give every current/future caller one stable
place to ask: What is this constraint? What does it affect? Which setting
controls it? Where is it currently implemented?
"""

from copy import deepcopy


def _hc(rule_id, name, description, components, *,
        config_keys=(), implementation=None, status="enforced",
        emits_violation=True, notes=""):
    return {
        "id": rule_id,
        "name": name,
        "type": "hard",
        "description": description,
        "affected_components": list(components),
        "config_keys": list(config_keys),
        "implementation": implementation,
        "status": status,
        "blocking": True,
        "emits_violation": emits_violation,
        "notes": notes,
    }


def _sc(rule_id, name, description, components, *,
        weight_key=None, default_weight=None, implementation="_fitness",
        optimization="preference", notes=""):
    return {
        "id": rule_id,
        "name": name,
        "type": "soft",
        "description": description,
        "affected_components": list(components),
        "weight_key": weight_key,
        "default_weight": default_weight,
        "implementation": implementation,
        "status": "scored",
        "blocking": False,
        "emits_violation": False,
        "optimization": optimization,
        "notes": notes,
    }


HARD_CONSTRAINTS = {
    "HC1": _hc(
        "HC1", "Regular Teaching Hours",
        "Full-time Permanent/Temporary faculty regular classes must stay within "
        "their weekday regular-teaching window (default 7:30 AM-4:30 PM). "
        "When an authorized 7:30-9:00 AM PT/TS class exists on that day, the "
        "remaining regular window shifts to 9:00 AM-6:00 PM.",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_load_enabled", "hc_pt_am_start", "hc_pt_am_end"),
        implementation="CSPValidator._check_time_windows",
    ),
    "HC2": _hc(
        "HC2", "Designee Regular Teaching Hours",
        "Regular-classified assignments of faculty designees/administrators must "
        "stay within the applicable designee weekday regular window (default "
        "8:00 AM-5:00 PM, subject to the scheduler's existing designee policy).",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_load_enabled",),
        implementation="CSPValidator._check_time_windows",
    ),
    "HC3": _hc(
        "HC3", "Full-Time Extra Teaching Load",
        "Validates extra/PT/teaching-substitution teaching windows. Permanent/"
        "Temporary faculty may use the authorized weekday AM PT window and the "
        "configured evening PT window; weekend PT/TS must stay within the "
        "scheduler's allowed weekend teaching window. The same time-window "
        "check also validates configured Part-Time faculty weekday availability.",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_load_enabled", "hc_pt_am_start", "hc_pt_am_end"),
        implementation="CSPValidator._check_time_windows",
    ),
    "HC4": _hc(
        "HC4", "Designee Extra Teaching Load",
        "PT-classified weekday assignments of faculty designees/administrators "
        "must fall within the authorized AM extra-teaching window or the "
        "designee-specific PM window (default 4:30-6:00 PM).",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_load_enabled", "hc_pt_am_start", "hc_pt_am_end",
                     "hc4_pt_pm_start", "hc4_pt_pm_end"),
        implementation="CSPValidator._check_designee_pt_window",
    ),
    "HC5": _hc(
        "HC5", "Restricted-Day Subject Requirement",
        "On configured restricted day(s), only the allowed subject group is "
        "scheduled. Under the current restricted-subject policy this is NSTP/OU; "
        "the restriction can be disabled/configured through scheduler settings.",
        ("day",),
        config_keys=("hc_weekend_enabled", "hc_weekend_day", "hc_weekend_subject"),
        implementation="CSPValidator._check_sunday_restriction",
    ),
    "HC6": _hc(
        "HC6", "Standard Time-Slot Compliance",
        "Class start and end times must use the configured/standard scheduling "
        "grid recognized by the scheduler.",
        ("time",),
        config_keys=("hc_time_blocks_enabled", "hc_time_slots"),
        implementation="CSPValidator._check_standard_slots",
    ),
    "HC7": _hc(
        "HC7", "Required Day Pairing",
        "A subject/class part that spans exactly two teaching days must use one "
        "of the configured valid day pairs (default Mon-Thu, Tue-Fri, Wed-Sat).",
        ("day", "time"),
        config_keys=("hc_day_pairing_enabled", "hc_day_pairs"),
        implementation="CSPValidator._check_day_pairing",
        notes="May be skipped by callers during draft-stage validation and enforced at publish/final validation.",
    ),
    "HC8": _hc(
        "HC8", "Designee PT Teaching-Night Limit",
        "Limits a designee's PT-classified weekday evening teaching to the "
        "configured maximum number of unique teaching nights per week. Multiple "
        "classes on the same evening count as one night; AM PT does not count.",
        ("instructor",),
        config_keys=("hc_faculty_load_enabled", "hc7_max_night"),
        implementation="CSPValidator._check_night_pt_cap",
    ),
    "HC9": _hc(
        "HC9", "Teaching Load Limit",
        "Prevents faculty teaching load from exceeding the applicable Regular, "
        "Part-Time, and Teaching-Substitution limits. Existing cross-section load "
        "can be included and valid merged/shared sessions are counted consistently "
        "with HC17.",
        ("instructor",),
        config_keys=("hc_faculty_load_enabled",),
        implementation="CSPValidator._check_load_limits",
    ),
    "HC10": _hc(
        "HC10", "Faculty Schedule Conflict",
        "Prevents incompatible overlapping class assignments for the same faculty, "
        "while respecting the scheduler's valid merged/shared-class rules.",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_conflict_enabled",),
        implementation="CSPValidator._check_faculty_overlaps",
    ),
    "HC11": _hc(
        "HC11", "Room Schedule Conflict",
        "Prevents incompatible overlapping use of the same room, while respecting "
        "valid merged/shared-class rules.",
        ("room", "day", "time"),
        config_keys=("hc_room_conflict_enabled",),
        implementation="CSPValidator._check_room_overlaps",
    ),
    "HC12": _hc(
        "HC12", "Section Schedule Conflict",
        "Prevents the same section from having overlapping class assignments, "
        "subject to the scheduler's valid merged/shared-class handling.",
        ("day", "time"),
        implementation="CSPValidator._check_section_overlaps",
        notes="Currently always enforced by CSPValidator.validate(); the legacy hc_section_conflict_enabled key is not read.",
    ),
    "HC13": _hc(
        "HC13", "Laboratory Room Requirement",
        "Requires laboratory class parts to use rooms recognized as Laboratory rooms.",
        ("room",),
        config_keys=("hc_lab_session_enabled",),
        implementation="CSPValidator._check_lab_room",
    ),
    "HC14": _hc(
        "HC14", "Room Capacity Requirement",
        "Checks whether the assigned room can accommodate the class when usable "
        "class-size/capacity data is available. The current scheduler documents "
        "this as non-blocking/no-op when class-size data is unavailable.",
        ("room",),
        config_keys=("hc_capacity_enabled",),
        implementation="CSPValidator._check_room_capacity",
        status="conditional",
    ),
    "HC15": _hc(
        "HC15", "Cross-Schedule Conflict Validation",
        "Prevents conflicts against assignments outside the schedule currently "
        "being edited/generated, such as other relevant published/draft schedule "
        "context. The current implementation is still distributed across app.py "
        "call sites rather than one CSPValidator method.",
        ("room", "instructor", "day", "time"),
        implementation="app.py cross-schedule validation call sites",
        status="distributed",
        emits_violation=False,
        notes="Catalogued here now; behavioral consolidation is intentionally deferred until the later migration phase.",
    ),
    "HC16": _hc(
        "HC16", "Merged-Class Validity",
        "Defines whether two simultaneous assignments are a legitimate merged/"
        "shared class. It is a decision primitive used by overlap/load logic, not "
        "a standalone violation-producing rule.",
        ("instructor", "room", "day", "time"),
        config_keys=(
            "hc_merge_enabled", "hc_merge_scope", "hc_merge_scope_subjects",
            "hc_merge_section_pairs",
        ),
        implementation="CSPValidator.is_valid_merge -> faculty_load.is_valid_merge",
        status="decision_primitive",
        emits_violation=False,
        notes=(
            "Merge eligibility is policy-gated by hc_merge_enabled and merge scope; "
            "hc_merge_scope_subjects, when configured, takes precedence over the "
            "legacy hc_merge_scope preset. hc_merge_section_pairs optionally "
            "restricts which section pairs may merge."
        ),
    ),
    "HC17": _hc(
        "HC17", "Merged-Class Faculty Load",
        "Ensures valid merged/shared classes contribute faculty teaching hours "
        "correctly rather than being double-counted. Its behavior is folded into "
        "HC9's load calculation rather than emitted as a separate violation.",
        ("instructor",),
        implementation="CSPValidator._check_load_limits",
        status="folded_into_HC9",
        emits_violation=False,
    ),
}


SOFT_CONSTRAINTS = {
    "SC1": _sc(
        "SC1", "Minimize Unnecessary Night Classes",
        "Penalizes avoidable late placement of Regular-classified classes. Required "
        "Part-Time/extra-teaching placements are exempt from this preference.",
        ("instructor", "day", "time"),
        weight_key="sc1_daytime", default_weight=20,
        optimization="minimize avoidable night placement",
    ),
    "SC2": _sc(
        "SC2", "Minimize Faculty Schedule Gaps",
        "Penalizes same-day gaps greater than 90 minutes between a faculty member's "
        "consecutive scheduled classes.",
        ("instructor", "day", "time"),
        weight_key="sc4_compact", default_weight=10,
        optimization="minimize long same-day gaps",
    ),
    "SC3": _sc(
        "SC3", "Balance Teaching-Day Distribution",
        "Penalizes heavily concentrated faculty teaching days when a day's assigned "
        "hours exceed twice that faculty's average scheduled hours across teaching days.",
        ("instructor", "day"),
        weight_key="sc3_day_dist", default_weight=10,
        optimization="balance teaching hours across days",
    ),
    "SC4": _sc(
        "SC4", "Balance Faculty/Part-Time Load",
        "Prefers a balanced use of faculty Part-Time load relative to the configured "
        "PT allowance without rewarding load-limit violations. Counting is merge-aware "
        "and aligned with HC9/HC16 behavior.",
        ("instructor",),
        weight_key="sc5_pt_balance", default_weight=10,
        optimization="balance PT teaching load",
    ),
    "SC5": _sc(
        "SC5", "Avoid Excessive Consecutive Teaching",
        "Penalizes excessive same-day consecutive teaching when the consecutive span "
        "reaches at least four hours.",
        ("instructor", "day", "time"),
        weight_key="sc7_consecutive", default_weight=30,
        optimization="minimize excessive consecutive teaching",
    ),
    "SC6": _sc(
        "SC6", "Minimize Unnecessary Weekend Use",
        "Penalizes avoidable weekend scheduling. Weekend placement required by NSTP/OU "
        "or Part-Time faculty availability is exempt from this preference.",
        ("instructor", "day"),
        weight_key="sc6_weekend", default_weight=10,
        optimization="minimize avoidable weekend placement",
    ),
    "SC7": _sc(
        "SC7", "Minimize Room/Building Movement",
        "Penalizes a faculty member moving between different known buildings during "
        "tight same-day transitions of 15 minutes or less.",
        ("instructor", "room", "day", "time"),
        weight_key="sc7_building", default_weight=15,
        optimization="minimize tight cross-building movement",
    ),
    "SC8": _sc(
        "SC8", "Historical Assignment Retention",
        "Rewards retention/alignment with historical or CBR-seeded assignment patterns "
        "as implemented by the current GA fitness function.",
        ("instructor", "room", "day", "time"),
        weight_key=None, default_weight=None,
        optimization="maximize historical assignment retention",
        notes="The current implementation is retained unchanged; no new weight/config behavior is introduced by this registry.",
    ),
    "SC9": _sc(
        "SC9", "Faculty Specialization Match",
        "Rewards faculty-subject specialization matches and penalizes mismatches using "
        "the scheduler's existing specialization mapping. This is the final soft-"
        "constraint home of the former HC_SPEC advisory concept.",
        ("instructor",),
        weight_key="sc9_specialization", default_weight=15,
        optimization="maximize faculty-subject specialization compatibility",
    ),
}


CONSTRAINTS = {**HARD_CONSTRAINTS, **SOFT_CONSTRAINTS}
HARD_CONSTRAINT_IDS = tuple(HARD_CONSTRAINTS)
SOFT_CONSTRAINT_IDS = tuple(SOFT_CONSTRAINTS)
ALL_CONSTRAINT_IDS = HARD_CONSTRAINT_IDS + SOFT_CONSTRAINT_IDS


def get_constraint(rule_id: str):
    """Return a defensive copy of one constraint definition, or None."""
    item = CONSTRAINTS.get(rule_id)
    return deepcopy(item) if item is not None else None


def get_hard_constraints() -> dict:
    """Return a defensive copy of the final HC1-HC17 catalog."""
    return deepcopy(HARD_CONSTRAINTS)


def get_soft_constraints() -> dict:
    """Return a defensive copy of the final SC1-SC9 catalog."""
    return deepcopy(SOFT_CONSTRAINTS)


def get_all_constraints() -> dict:
    """Return a defensive copy of the complete final constraint catalog."""
    return deepcopy(CONSTRAINTS)
