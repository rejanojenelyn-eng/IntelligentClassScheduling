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
        notes="Also enforced for Local Scheduler adjustments against the faculty's effective "
              "schedule (app._validate_local_faculty_service_rules), same rule method.",
    ),
    "HC2": _hc(
        "HC2", "Designee Regular Teaching Hours",
        "Regular-classified assignments of faculty designees/administrators must "
        "stay within the applicable designee weekday regular window (default "
        "8:00 AM-5:00 PM, subject to the scheduler's existing designee policy).",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_load_enabled",),
        implementation="CSPValidator._check_time_windows",
        notes="Also enforced for Local Scheduler adjustments (same rule method).",
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
        notes="Also enforced for Local Scheduler adjustments (same rule method).",
    ),
    "HC4": _hc(
        "HC4", "Designee Extra Teaching Load",
        "PT-classified weekday assignments of faculty designees/administrators "
        "must fall within the authorized AM extra-teaching window, the "
        "designee-specific PM window (default 4:30-6:00 PM), or the 6:00-9:00 PM "
        "Night Teaching Service window. Whether a designee may use the night "
        "window, and on how many days, is decided by HC8.",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_load_enabled", "hc_pt_am_start", "hc_pt_am_end",
                     "hc4_pt_pm_start", "hc4_pt_pm_end"),
        implementation="CSPValidator._check_designee_pt_window",
        notes="Also enforced for Local Scheduler adjustments (same rule method).",
    ),
    "HC5": _hc(
        "HC5", "Restricted-Day Subject Requirement",
        "On configured restricted day(s) (default Sunday), only NSTP/OU subjects "
        "may be scheduled; hc_weekend_subject='all_allowed' lifts the restriction "
        "and hc_weekend_enabled disables it. The same rule applies to generation, "
        "manual/publish validation and Local Scheduler adjustments.",
        ("day",),
        config_keys=("hc_weekend_enabled", "hc_weekend_day", "hc_weekend_subject"),
        implementation="CSPValidator._check_sunday_restriction",
        notes="Single definition: scheduler.subject_allowed_on_restricted_day / "
              "restricted_day_prefixes, shared by CSPValidator and Local validation.",
    ),
    "HC6": _hc(
        "HC6", "Standard Time-Slot Compliance",
        "A class time must be one whole valid start-end block: a built-in "
        "scheduler.STANDARD_BLOCKS block or an admin-configured hc_time_slots block. "
        "A start and an end that each belong to some block are not enough -- the "
        "pair itself must be a block. Adjacent blocks (e.g. 7:30-9:00 and 9:00-10:30) "
        "touch but do not overlap. Applies to Local Scheduler adjustments too.",
        ("time",),
        config_keys=("hc_time_blocks_enabled", "hc_time_slots"),
        implementation="CSPValidator._check_standard_slots",
        notes="15:00-18:00 is a confirmed institutional block configured through "
              "hc_time_slots (not a STANDARD_BLOCKS generator block); see "
              "migrations/2026-09-30_hc6_time_slot_1500_1800.sql.",
    ),
    "HC7": _hc(
        "HC7", "Required Day Pairing",
        "A subject/class part that spans exactly two teaching days must use one "
        "of the configured valid day pairs (default Mon-Thu, Tue-Fri, Wed-Sat).",
        ("day", "time"),
        config_keys=("hc_day_pairing_enabled", "hc_day_pairs"),
        implementation="CSPValidator._check_day_pairing",
        notes=("Callers may pass skip_rules={'HC7'}; Save Draft reports it as a non-blocking "
               "warning and Publish blocks on it. Cross-entry pairing is evaluated per section and "
               "class type; rows from different sections offering the same subject are never "
               "combined into a synthetic day pair. INTENTIONALLY NOT APPLIED to Local Scheduler "
               "adjustments: a Local override moves one occurrence of an established Official "
               "assignment, so temporarily breaking the Official day pair is expected, not a defect."),
    ),
    "HC8": _hc(
        "HC8", "Designee PT Teaching-Night Limit",
        "DESIGNEE_NIGHT_TEACHING_DAY_LIMIT. A designee's designation 'PT/Night "
        "Teaching Service' value is the maximum number of DISTINCT days per week "
        "they may teach inside the 6:00-9:00 PM Night Teaching Service window. "
        "0 means the night window is not available. Multiple classes on the "
        "same day count as one night; a subject meeting on two days uses two. "
        "4:30-6:00 PM and AM PT never count as a night.",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_load_enabled",),
        implementation="CSPValidator._check_night_pt_cap",
        notes="Per-designation value: designation.nightteachingservice. The "
              "legacy global hc7_max_night key no longer controls this rule. Across "
              "sections it is checked at Save Draft/Publish (app._check_designee_night_limit, "
              "fail-closed at Publish) and for Local adjustments against the designee's "
              "effective schedule, blocking only when the adjustment ADDS a night over the limit.",
    ),
    "HC9": _hc(
        "HC9", "Teaching Load Limit",
        "Prevents faculty teaching load from exceeding the applicable Regular, "
        "Part-Time, and Teaching-Substitution limits. Load counts real scheduled hours, "
        "merge-aware per HC17, Draft-preferred (a subject's Draft replaces its Published "
        "version, never both). Other sections' committed load is supplied bucketed "
        "(faculty_load.get_faculty_load_batch): Regular hours are added to the Regular "
        "bucket and PT hours to the PT bucket; a legacy plain-number load is counted once "
        "against total capacity. Only the current section is excluded when it is known, so "
        "sibling sections count. The Publish total-load gate uses the same merge-aware batch "
        "and is fail-closed.",
        ("instructor",),
        config_keys=("hc_faculty_load_enabled",),
        implementation="CSPValidator._check_load_limits",
        notes=("INTENTIONALLY NOT APPLIED to Local Scheduler adjustments: a Local override changes "
               "only an occurrence's placement; assignment-load ownership stays on the Official "
               "schedule, so it never adds or doubles load. Approved Make-up classes are one-date "
               "meetings and never add teaching load either. Known deferred items: the Teaching "
               "Substitution allowance can absorb both Regular and PT overflow; a Draft that "
               "reassigns a subject to another faculty still counts the old faculty's Published row."),
    ),
    "HC10": _hc(
        "HC10", "Faculty Schedule Conflict",
        "Prevents the same faculty from teaching two independent effective class "
        "occurrences at overlapping times (start_a < end_b AND end_a > start_b; exact "
        "end-to-start boundaries are not overlaps). Exempt when the two occurrences are "
        "one valid HC16 merged/shared class OR both are NSTP/OU (the long-standing "
        "shared-faculty exemption, independent of merge policy). A missing/TBA faculty "
        "never conflicts.",
        ("instructor", "day", "time"),
        config_keys=("hc_faculty_conflict_enabled",),
        implementation="CSPValidator._check_faculty_overlaps",
        notes=("One shared exemption rule, faculty_load.faculty_overlap_exempt, used by "
               "CSPValidator, cross-schedule (HC15) checks, Local conflict checking, Local "
               "Publish and request validation, so the same scenario gives the same result. "
               "hc_faculty_conflict_enabled: Admin Settings switch; it gates OFFICIAL scheduling only (generation, Manual Editor, Save Draft, Publish incl. cross-schedule). Local Scheduler, Make-up / Schedule Adjustment requests and approvals never read it and stay protected."),
    ),
    "HC11": _hc(
        "HC11", "Room Schedule Conflict",
        "Prevents the same physical room from hosting two independent effective class "
        "occurrences at overlapping times. Exact end-to-start boundaries are not "
        "overlaps; a TBA room never conflicts; occurrences forming one valid HC16 "
        "merged/shared class may share a room.",
        ("room", "day", "time"),
        config_keys=("hc_room_conflict_enabled",),
        implementation="CSPValidator._check_room_overlaps",
        notes="hc_room_conflict_enabled: Admin Settings switch; it gates OFFICIAL scheduling only (generation, Manual Editor, Save Draft, Publish incl. cross-schedule). Local Scheduler, Make-up / Schedule Adjustment requests and approvals never read it and stay protected.",
    ),
    "HC12": _hc(
        "HC12", "Section Schedule Conflict",
        "Prevents the same student section from being required in two independent "
        "effective class occurrences at overlapping times. Different faculty or "
        "rooms do not remove a section conflict, HC16 never exempts it, and exact "
        "end-to-start boundaries do not overlap. Official Publish also checks the "
        "submitted rows against the same section's carried-forward subjects.",
        ("day", "time"),
        config_keys=("hc_section_conflict_enabled",),
        implementation="CSPValidator._check_section_overlaps",
        notes="hc_section_conflict_enabled: Admin Settings switch; it gates OFFICIAL scheduling only (generation, Manual Editor, Save Draft, Publish incl. cross-schedule). Local Scheduler, Make-up / Schedule Adjustment requests and approvals never read it and stay protected.",
    ),
    "HC13": _hc(
        "HC13", "Laboratory Room Requirement",
        "A subject with laboratory hours must have its lab part in a Laboratory room. "
        "In a generated/manual schedule (CSPValidator) at least one of the subject's "
        "sessions must be in a Laboratory room; a TBA room defers the check. For a Local "
        "adjustment the moved occurrence needs a Laboratory room only if it is the lab "
        "part (its Official occurrence was in a Laboratory room, or the subject has no "
        "lecture part); the lecture part of a lecture+lab subject may use a lecture room.",
        ("room",),
        config_keys=("hc_lab_session_enabled",),
        implementation="CSPValidator._check_lab_room",
        notes="Laboratory room type is decided by the shared scheduler.is_laboratory_room_type.",
    ),
    "HC14": _hc(
        "HC14", "Room Capacity Requirement",
        "CURRENTLY INACTIVE / NOT ENFORCEABLE. The rule would check that the assigned "
        "room can accommodate the expected class size, but the system has no class-size "
        "or expected-enrollment data (no such field exists on sections, curriculum "
        "subjects or schedules, and no caller supplies one), so it never produces a "
        "violation today and no schedule is ever blocked by it. Missing enrollment data "
        "never creates a guessed capacity violation. If enrollment data is introduced "
        "later, a valid same-room merged class must be compared against the aggregate "
        "expected enrollment of the distinct participating sections rather than "
        "validating each section independently.",
        ("room",),
        config_keys=("hc_capacity_enabled",),
        implementation="CSPValidator._check_room_capacity",
        status="conditional",
        notes=(
            "Status 'conditional' = the check only runs when a class_size/"
            "expected_enrollment value is supplied, which never happens with the current "
            "schema, so the hc_capacity_enabled toggle has no practical effect. Do not "
            "infer enrollment from room capacity or multiply a single unknown section "
            "size. Multi-room overflow merges require explicit per-room attendance/"
            "allocation data before capacity can be enforced safely. Implementing Room "
            "Capacity is a separate future requirement (needs enrollment data)."
        ),
    ),
    "HC15": _hc(
        "HC15", "Cross-Schedule Conflict Validation",
        "Prevents conflicts against assignments outside the schedule currently "
        "being edited/generated. Operational checks use the effective schedule: "
        "Published Official occurrences minus occurrences superseded by active "
        "Published Local overrides, plus those Published Local overrides. Draft and "
        "Archived Local arrangements are never operational; the Local draft currently "
        "being validated is overlaid as a proposed effective state. Request validation "
        "(Make-up, Schedule Adjustment) and Academic Head approval use the same effective "
        "schedule; approved Make-ups occupy only their own date.",
        ("room", "instructor", "day", "time"),
        config_keys=("hc_cross_schedule_enabled",),
        implementation="app.py cross-schedule validation call sites",
        status="distributed",
        emits_violation=False,
        notes=("Shared effective/proposed-effective state semantics live in "
               "constraints.context_conflicts; feature-specific SQL scopes remain in app.py "
               "(_check_cross_schedule_conflicts, Local check/publish, _request_official_conflict "
               "/ _request_local_conflict / _request_makeup_occupancy). Official Publish runs the "
               "cross-schedule, total-load and night-limit checks fail-closed: if a check cannot "
               "read its data, Publish is refused rather than treated as conflict-free. "
               "hc_cross_schedule_enabled: Admin Settings switch; it gates OFFICIAL scheduling only (generation, Manual Editor, Save Draft, Publish incl. cross-schedule). Local Scheduler, Make-up / Schedule Adjustment requests and approvals never read it and stay protected."),
    ),
    "HC16": _hc(
        "HC16", "Merged-Class Validity",
        "Defines whether simultaneous assignments are one legitimate merged/shared "
        "class rather than independent bookings: same subject, configured merge "
        "scope, allowed section pairing when configured, and faculty-sharing policy "
        "(NSTP/OU may use different faculty; other eligible subjects require the same faculty). "
        "Room equality is not part of merge identity.",
        ("instructor", "room", "day", "time"),
        config_keys=(
            "hc_merge_enabled", "hc_merge_scope", "hc_merge_scope_subjects",
            "hc_merge_section_pairs",
        ),
        implementation="CSPValidator.is_valid_merge -> faculty_load.is_valid_merge",
        status="decision_primitive",
        emits_violation=False,
        notes=(
            "Two models, selected by the internal hc_merge_model switch (default 'legacy'). "
            "LEGACY (live): the scope/pair rules described here, plus the separate NSTP/OU "
            "shared-faculty exemption. GROUPS: merge_groups.GroupMergePolicy over the "
            "administrator-configured Merge Groups is the only merge interpretation — an "
            "HC10/HC11 overlap is waived only between two different sections' occurrences of "
            "the same usable merged event (same group AND same meeting: exact day, start, end, "
            "room) whose faculty mode they satisfy; no scope, pairs or NSTP/OU prefix rule; "
            "HC16 then also emits HC16_DIVERGED / HC16_FACULTY (invalid) and HC16_UNSCHEDULED / "
            "HC16_FACULTY_TBA (incomplete) consistency violations. HC12 is never waived. "
            "Merge eligibility is policy-gated by hc_merge_enabled and merge scope; "
            "hc_merge_scope_subjects, when configured, takes precedence over the "
            "legacy hc_merge_scope preset. hc_merge_section_pairs optionally "
            "restricts which section pairs may merge. Published mergedclass synchronization "
            "also applies HC16. Multi-faculty NSTP/OU shared sessions remain valid operational "
            "HC16 overlaps but are not collapsed into mergedclass, whose single employeenumber "
            "is used as a GA faculty pre-assignment."
        ),
    ),
    "HC17": _hc(
        "HC17", "Merged-Class Faculty Load",
        "Ensures valid merged/shared classes contribute faculty teaching hours "
        "correctly rather than being double-counted: a same-faculty merged meeting "
        "counts once (3+ sections only when every section pair is HC16-valid), and "
        "different-faculty NSTP/OU shared sessions give each instructor their own hours. "
        "A meeting already counted in another section's load is not counted again. Its "
        "behavior is folded into HC9's load calculation rather than emitted as a "
        "separate violation. A Local override of an Official occurrence does not add "
        "another teaching assignment or double the faculty load; it only changes the "
        "occurrence placement.",
        ("instructor",),
        implementation="CSPValidator._check_load_limits",
        status="folded_into_HC9",
        emits_violation=False,
    ),
}


SOFT_CONSTRAINTS = {
    "SC1": _sc(
        "SC1", "Minimize Unnecessary Night Classes",
        "Penalizes avoidable late placement of Regular-classified classes: a "
        "Regular-classified class of a non-Part-Time faculty that ends after 4:30 PM. "
        "Required Part-Time/extra-teaching placements are exempt from this preference.",
        ("instructor", "day", "time"),
        weight_key="sc1_daytime", default_weight=20,
        optimization="minimize avoidable night placement",
        notes=("Under the default 7:30 AM-4:30 PM regular window a Regular-classified class "
               "already ends by 4:30 PM, so SC1 only has an effect for faculty types configured "
               "with a later regular_end (deliberate; see test_characterization_soft_constraints)."),
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
        "Prefers PT teaching hours close to the faculty's configured PT allowance: the "
        "penalty is weight x |PT hours - parttimeload|, so both under- and over-use move "
        "the score away from the target. HC9 remains the hard limit. Counting is "
        "merge-aware and aligned with HC9/HC16 behavior.",
        ("instructor",),
        weight_key="sc5_pt_balance", default_weight=10,
        optimization="balance PT teaching load",
        notes=("Counts weekday classes ending after the regular window within the schedule being "
               "scored (not other sections' load)."),
    ),
    "SC5": _sc(
        "SC5", "Avoid Excessive Consecutive Teaching",
        "Penalizes excessive continuous teaching. A faculty's same-day classes form one "
        "continuous run while the gap between them is 15 minutes or less; a larger gap "
        "starts a new run, and any number of back-to-back classes (three or more "
        "included) is one run. Each run of two or more classes spanning 4 hours or more "
        "(first start to last end) costs the weight once. A single class is not "
        "consecutive teaching (its length is governed by HC6).",
        ("instructor", "day", "time"),
        weight_key="sc7_consecutive", default_weight=30,
        optimization="minimize excessive consecutive teaching",
        notes="scheduler.teaching_runs; SC5_CONTIGUITY_GAP_MINUTES=15, SC5_EXCESSIVE_RUN_MINUTES=240.",
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
