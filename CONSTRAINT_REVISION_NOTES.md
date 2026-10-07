# Constraint Revision Notes — Effective Schedule / Double Booking / Merge

## Implemented
- HC15 now has reusable effective/proposed-effective schedule semantics in `constraints/context_conflicts.py`.
- Effective schedule = Published Official not superseded by active Published Local + active Published Local overrides.
- Draft Local arrangements do not reserve resources; a draft under validation is treated as a proposed overlay by `official_sessionid`.
- Added one half-open interval overlap primitive: `start_a < end_b AND end_a > start_b`; exact end-to-start boundaries do not conflict.
- Local candidate-vs-candidate checking now uses that shared overlap primitive.
- HC10/HC11/HC12 registry wording now explicitly defines faculty, room, and section double-booking against effective occurrences.
- HC16 wording now matches the implemented authoritative merge decision: same subject, configured scope/pairs, NSTP/OU different-faculty allowance, same-faculty requirement for other eligible subjects, and room-independent merge identity.
- HC17 remains folded into HC9; the existing merge-aware load implementation was preserved instead of duplicated.

## Existing behavior preserved
- Local Arrangements remain partial occurrence-bound overrides through `official_sessionid`.
- Published Local overrides vacate only the exact Official occurrence they replace.
- Self-conflict exclusion remains occurrence-bound.
- Existing HC16 merge-aware generation/publish paths and HC17/HC9 load handling remain authoritative.

## Verification
- `python -m py_compile app.py constraints/context_conflicts.py constraints/registry.py` passes for every revised Python file.
- Dependency-free HC15 helper smoke tests pass.
- The full pytest suite could not run in this isolated runtime because Flask/psycopg2 are not installed, and this runtime cannot reach PyPI to install the project's requirements.
- Whole-tree `compileall` also encounters a pre-existing untouched `app_jaja.py` containing null bytes; that legacy copy was not modified in this revision.

## Next revision — Local HC10/HC11/HC12 now consume HC16
- Local Scheduler conflict validation now loads the active merge policy once and uses the authoritative HC16 `is_valid_merge` decision for candidate-vs-candidate, candidate-vs-Official, and candidate-vs-Published-Local checks.
- Added one shared `overlapping_resource_conflicts` helper: a valid HC16 merge exempts shared Faculty/Room occupancy, but never suppresses a same-section HC12 collision.
- Added section/program identity to effective occupancy rows so configured merge-section pairings can be evaluated instead of silently losing section context.
- Added dependency-free regression checks for merge-aware Faculty/Room/Section resource conflict classification.


## Next revision — HC9/HC17 load semantics + HC7 section-safe pairing
- Confirmed the existing Local Scheduler contract: Local arrangements change occurrence placement only and MUST NOT create a second teaching assignment or increase faculty load. The authoritative faculty-load endpoints therefore remain Official-assignment based.
- HC9 and HC17 registry wording now states this explicitly so future effective-calendar work cannot accidentally double-count Official + Local replacement rows.
- Fixed HC7 cross-entry day-pair validation to group by section as well as subject/class type. Multi-section/effective-schedule payloads no longer combine the same subject from different sections into a fictitious day pair.
- Added regression tests proving different sections stay independent while two entries of the same section are still paired and validated.
- HC14 remains intentionally conditional/no-op until real expected enrollment/class-size data exists; no guessed merged-section capacity rule was introduced.

## V4 — End-to-end HC15 consistency
- Generation cross-schedule validation now uses the effective schedule, not raw Published Official rows: active Published Local overrides remove their exact Official occurrence from occupancy and are themselves included as occupancy.
- Generation cross-schedule validation now routes Faculty/Room/Section resource decisions through the shared `overlapping_resource_conflicts` primitive and HC16 merge decision.
- Merge decisions in generation cross-check now carry program/section identity so configured section-pair rules can be honored.
- Publish no longer maintains a separate room-only HC15 implementation. It reuses `_check_cross_schedule_conflicts`, so Generate and Publish evaluate the same effective occupancy and HC10/HC11/HC12 + HC16 semantics.
- Draft Local rows remain non-effective and do not reserve resources.

## V5 — Coordinated Local moves / swaps / chains
- Fixed Local conflict validation so one submitted Local payload is evaluated as one proposed transaction, not as isolated row edits against stale Official occupancy.
- All `official_sessionid` values replaced by the current proposal are excluded from the pre-edit Official occupancy while validating every candidate.
- Existing Published Local rows for the same section and any occurrence replaced by the proposal are likewise excluded before candidate checks.
- This permits valid A↔B swaps and A→B→C chained moves when the resulting state has no HC10/HC11/HC12 conflict, while still blocking conflicts against unaffected effective occurrences.
- Drafts remain non-effective outside their own validation transaction; archive behavior remains unchanged and restores the underlying Official occurrence to effective occupancy.


## V6 — Constraint-path audit (Manual Editor / Local mode)

- Audited Generation, Publish, Local conflict API, room calendar, faculty calendar, and section calendar call paths.
- Fixed `/api/manual/faculty_schedule` so Local mode now returns the HC15 effective schedule: Published Official minus exact occurrences displaced by active Published Local overrides, plus active Published Local overrides. Draft Local arrangements do not reserve or vacate resources.
- Added section/program/year/faculty/source identity to faculty occupancy rows so HC16/UI conflict context has enough identity data.
- Updated both the external Manual Editor JS and the inline template path to pass `scheduler_mode` to faculty and section occupancy endpoints.
- Updated the external JS section conflict request to pass the actual `section_id`; sibling sections in the same program/year must not be treated as HC12 conflicts merely because they share program/year.
- Room occupancy was already effective-schedule aware in Local mode and was retained.
- Generation/Publish continue to use `_check_cross_schedule_conflicts`; Local candidate validation continues to use the centralized `context_conflicts` resource primitive and HC16 adapter.


## V7 — HC16 persistence audit
- `_sync_mergedclass_for_semester` now applies the authoritative HC16 merge scope and configured section-pair rules before materializing `mergedclass`.
- Groups of 3+ sections require every pair to be HC16-valid; transitive A-B/B-C permission no longer silently persists A-B-C when A-C is not allowed.
- Same-subject/same-faculty overlap outside merge scope is no longer automatically persisted as a merged class.
- Multi-faculty NSTP/OU shared sessions remain valid operational HC16 overlaps, but are intentionally not collapsed into `mergedclass` because that table has one `employeenumber` and the GA consumes it as a faculty pre-assignment.
- Room remains excluded from persisted merge identity, preserving overflow-room support.

## V8 — HC17 / HC9 merged faculty-load audit
- Made `faculty_load.group_assignments()` require full pairwise HC16 authorization for 3+ section merge groups. A-B + A-C no longer deduplicates the whole group when B-C is not authorized.
- Applied the same full-group HC16 rule inside `CSPValidator._check_load_limits()` so GA/post-hoc HC9 and Faculty Load calculations agree.
- Kept different-faculty NSTP/OU shared sessions separate for load purposes: HC17 deduplication is per faculty, so each instructor receives their own actual teaching hours.
- Made the batch existing-load path merge-aware. `get_faculty_hours_batch()` now retrieves detailed slices and applies the same `group_assignments(..., config=...)` HC17 logic instead of raw SQL SUM that double-counted one same-faculty merged meeting once per section.
- Faculty teaching-load UI/API calls now pass the live scheduler configuration into `compute_load_buckets()`, enabling HC17 consistently outside CSP validation.
- Local teaching-session load rows now carry the real section identity/label, allowing configured merge section-pairs to be evaluated instead of using only program/year.
- Kept Local Scheduler placement overrides load-neutral: this phase does not add Official + Local hours together.
- Decoupled merge-scope parsing in `faculty_load.py` from the PostgreSQL module so HC16/HC17 load logic is unit-testable without a DB driver.
- Added `tests/test_hc17_merge_load_v8.py`; focused HC17 tests pass (3 passed).

## V9 — HC14 capacity audit + final centralized specification cleanup
- Audited the schema and all current scheduler callers for expected enrollment/class-size data. No reliable section enrollment field is currently supplied to HC14, so the validator remains intentionally conditional rather than inventing capacity violations.
- Finalized HC14 semantics in the centralized registry: missing enrollment never becomes a guessed violation; if reliable enrollment is added later, a same-room valid merged class must be checked using aggregate enrollment of its distinct participating sections.
- Explicitly documented that multi-room/overflow merged classes cannot be capacity-validated safely until per-room attendance/allocation data exists.
- No database migration or guessed section-size field was introduced.
- Added a final constraint specification document and registry regression guards so HC1-HC17 / SC1-SC9 wording and the special HC14/HC15/HC16/HC17 statuses remain discoverable and testable.

## V10 — Real-suite regression repair (2026-09-29)

Based on the user's real pytest run (934 passed / 11 failed / 1 skipped), V10 repairs the first concrete regressions instead of adding new constraint behavior.

- Fixed `api_local_check_room_conflicts` SQL placeholder/parameter mismatch that raised `IndexError: tuple index out of range`.
- Restored exact occurrence self-exclusion (`official_sessionid`) while retaining transaction-wide displacement for swaps/chained Local moves.
- Kept explicit HC10/HC11/HC12 conflict reporting in the Local endpoint while still applying HC16 to Faculty/Room merge exemptions.
- Publish revalidation now carries the complete set of Official occurrence IDs displaced by the Draft, while retaining exact self-exclusion.
- Generation's published/Draft occupancy loader now follows HC15 for Published Official rows: active Published Local replacements suppress their displaced Official occurrence; active Published Local rows are added as effective occupancy; Local Drafts remain non-effective.
- Faculty occupancy no longer disappears merely because the existing session has a TBA/null room.
- Updated regression tests whose old fixed expectations assumed raw Official occupancy rather than the revised Effective Schedule. The publish integration test now checks HC16 at the centralized `_check_cross_schedule_conflicts` adapter where it is actually implemented.

Static Local Scheduler regression subset: 22 passed. Full DB-backed pytest must be rerun in the user's normal environment.

## Constraint-fix pass (2026-09-30) — Phases 1–9

Audit-driven fixes; HC/SC IDs unchanged. Each phase has its own regression file (`tests/test_phase*_*.py`).

- **Phase 1 — crashes / fail-open.** `/api/requests/validate` was bound to the helper `_request_local_conflict` (every call returned HTTP 500); the route now points at `api_requests_validate`. Fixed the make-up `day` UnboundLocalError. Request Official queries are semester-scoped. Official Publish runs its HC8/HC9/HC15 cross-section checks fail-closed (`ConstraintCheckUnavailable` → 503, rolled back).
- **Phase 2 — HC9/HC17 load.** Existing load is bucketed (Regular/PT) and merge-aware (`faculty_load.get_faculty_load_batch`); a legacy numeric load is counted once instead of being added to both buckets; a meeting already counted in another section is not counted again; only the current section is excluded when known. The Publish total-load gate no longer uses a raw SQL SUM (which added Draft + Published versions and multiplied merged classes). `get_faculty_scheduled_hours` and `teaching_assignments` are merge-aware / HC16 section-pair aware. *Correction to V8 above:* the raw SUM had remained in the Publish load gate until this pass.
- **Phase 3 — HC10/HC16.** One exemption rule, `faculty_load.faculty_overlap_exempt` (valid HC16 merge OR NSTP/OU shared faculty), used by CSP, cross-schedule, Local check and Local Publish. Missing/TBA faculty never conflicts. HC12 is never merge-exempt.
- **Phase 4 — Local enforcement.** Local adjustments enforce HC1–HC4 and HC8 (against the effective schedule), HC5 (NSTP/OU, shared definition), HC6 and HC13 (lab part only). HC7 and HC9 are intentionally excluded.
- **Phase 5 — requests.** Request validation, faculty submission/preview and Academic Head approval use the Effective Schedule; approval revalidates before approving. A Make-up is one meeting on its date — approval no longer inserts a recurring Official session or adds load. Save Draft HC12 is section-scoped.
- **Phase 6 — Publish + HC6.** Publish checks submitted rows against the same section's carried-forward subjects (HC10/11/12); the incomplete-Draft gate is section-scoped. HC6 validates whole start–end blocks (`STANDARD_BLOCKS` + `hc_time_slots`); 15:00–18:00 is configured as a confirmed institutional block (`migrations/2026-09-30_hc6_time_slot_1500_1800.sql`).
- **Phase 7 — SC5.** Continuous teaching runs (gap ≤ 15 min; a run of 2+ classes spanning ≥ 4 h costs the weight once) replace the pairwise check.
- **Phases 8–9 — documentation.** HC14 documented as inactive (no enrollment data); registry, spec and comments aligned with the behavior above.

Deferred (not changed): Teaching Substitution allowance can absorb both Regular and PT overflow in HC9; a Draft that reassigns a subject still counts the old faculty's Published row; partial regeneration does not count the section's own unregenerated subjects; Retrieve Previous excludes by program+year; GA pre-seeding blocks slots purely by time (no HC16/NSTP awareness); legacy approved Make-ups still exist as weekly Official rows; DIT1 Draft times 10:30–13:00 / 7:30–12:30 are not valid blocks.
