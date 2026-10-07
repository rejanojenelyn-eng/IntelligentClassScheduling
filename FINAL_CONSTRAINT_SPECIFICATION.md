# ScheduLink — Final Central Constraint Specification

The authoritative machine-readable catalog is `constraints/registry.py`. This document is a human-readable summary of the behavior actually implemented, not a second source of truth. IDs HC1–HC17 and SC1–SC9 are stable.

## Core scheduling-state rule

Operational conflict validation uses the **Effective Schedule**:

`Published Official − Official occurrences superseded by active Published Local + active Published Local overrides`

- **Draft Local** is not operational: it neither vacates Official nor reserves anything.
- **Archived Local** is not operational: the Official occurrence becomes effective again.
- While validating one Local transaction, every occurrence it replaces is removed first and the proposed rows are overlaid (**Proposed Effective Schedule**).
- **Approved Make-up classes** are one-date meetings: they occupy only their own `requested_date` and never become weekly Official sessions or teaching load.

Used by generation, Manual/Publish cross-schedule checks (HC15), Local conflict checking and Local Publish, request validation and Academic Head approval.

## Hard constraints

| ID | Rule |
|---|---|
| HC1–HC4 | Faculty teaching windows (full-time regular, designee regular, full-time extra/Part-Time, designee extra). Also enforced for Local adjustments against the faculty's effective schedule. |
| HC5 | Restricted day (default Sunday): NSTP/OU only (`hc_weekend_subject='all_allowed'` lifts it). One shared definition for CSP and Local. |
| HC6 | A class time must be one whole valid **start–end block**: `STANDARD_BLOCKS` or a configured `hc_time_slots` block. Individually valid start and end times are not enough. 15:00–18:00 is a confirmed configured block. Applies to Local too. |
| HC7 | Required day pairing (Mon–Thu, Tue–Fri, Wed–Sat by default). Non-blocking at Save Draft, blocking at Publish. |
| HC8 | Designee night-teaching day limit (distinct 6–9 PM weekdays ≤ designation value). Local adjustments are checked against the effective schedule and fail only when they add a night over the limit. |
| HC9 | Teaching load limit: real hours, merge-aware (HC17), Draft-preferred, bucketed Regular/PT existing load, sibling sections counted. |
| HC10 | Faculty double-booking. Exempt for a valid HC16 merge or the NSTP/OU shared-faculty rule (`faculty_load.faculty_overlap_exempt`, one rule everywhere). Missing/TBA faculty never conflicts. |
| HC11 | Room double-booking. TBA room never conflicts; a valid HC16 merge may share a room. |
| HC12 | Section double-booking. Never merge-exempt. Official Publish also checks submitted rows against the same section's carried-forward subjects. |
| HC13 | Laboratory room for the lab part of a lab subject (CSP: at least one session in a Laboratory room; Local: only when the moved occurrence is the lab part). |
| HC14 | **Room capacity — INACTIVE / NOT ENFORCEABLE.** No class-size or enrollment data exists, so it never produces a violation. See below. |
| HC15 | Cross-schedule validation against the Effective Schedule. Official Publish runs its cross-section checks **fail-closed**. |
| HC16 | Merged-class validity: same subject, merge scope, allowed section pairs (every pair for 3+ sections), NSTP/OU may have different faculty, room not part of identity. |
| HC17 | Merged-class faculty load, folded into HC9: one same-faculty merged meeting counts once; different-faculty NSTP/OU sessions count per instructor. |

Time overlap is half-open: `start_a < end_b AND end_a > start_b`. Exact end-to-start boundaries (e.g. 7:30–9:00 and 9:00–10:30) do not conflict.

### Intentional Local Scheduler exclusions

- **HC7 Day Pairing** does not apply to Local overrides: a Local adjustment moves one occurrence of an established Official assignment, so temporarily breaking the Official day pair is expected.
- **HC9 Teaching Load** does not apply to Local overrides: Local changes placement only; load ownership stays on the Official schedule and is never added or doubled.

### HC14 status

HC14 is **inactive**. Rooms have a capacity, but the schema has no expected class size / enrollment for sections, and no caller supplies one, so the check is a structural no-op and the `hc_capacity_enabled` toggle has no practical effect. It must never guess enrollment. If enrollment data is introduced later, same-room merged capacity must use the aggregate expected enrollment of the distinct participating sections, and multi-room overflow needs explicit per-room attendance allocation.

## Soft constraints

| ID | Rule |
|---|---|
| SC1 | Unnecessary night classes: Regular-classified class of a non-Part-Time faculty ending after 4:30 PM (only possible with an extended regular window). |
| SC2 | Faculty schedule gaps > 90 minutes. |
| SC3 | Day distribution: a day with more than twice the faculty's average daily hours. |
| SC4 | PT load near the faculty's PT allowance: `weight × |PT hours − parttimeload|`. |
| SC5 | Excessive consecutive teaching: same-day classes with gaps ≤ 15 minutes form one continuous run; each run of two or more classes spanning ≥ 4 hours is penalized once. |
| SC6 | Unnecessary weekend use (NSTP/OU and Part-Time exempt). |
| SC7 | Building movement on tight (≤ 15 min) transitions. |
| SC8 | Historical/CBR assignment retention. |
| SC9 | Faculty specialization match (non-blocking). |
