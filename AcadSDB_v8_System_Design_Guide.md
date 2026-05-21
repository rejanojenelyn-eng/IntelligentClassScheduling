# AcadSDB v8.2 — Intelligent Class Scheduling System Design Guide

**Version:** 8.2  
**Date:** May 2026  
**System:** PUPLC Intelligent Class Scheduling System  
**Branch:** jajaBranch

### v8.2 Changes
- Replaced single Manual Editor with 2 dedicated editors (Final Schedule + Local Arrangement)
- Removed Published status — lifecycle is now Draft → Approved → Locked
- Adjustment Period applies to Final Schedule only — NOT Local Arrangement
- Local Arrangement only accessible after an Approved schedule exists
- Local Arrangement editable until semester ends (independent of Adjustment Period)
- Local Arrangement statuses: Pending / Active / Expired (auto at semester end)

---

## Table of Contents

1. [Overview](#1-overview)
2. [The Two Editors](#2-the-two-editors)
3. [Status Definitions](#3-status-definitions)
4. [Schedule Version Lifecycle](#4-schedule-version-lifecycle)
5. [Version State Rules](#5-version-state-rules)
6. [Adjustment Period Setting](#6-adjustment-period-setting)
7. [Lifecycle Scenarios](#7-lifecycle-scenarios)
8. [Auto-Generation Engine](#8-auto-generation-engine)
9. [Final Schedule Editor Rules](#9-final-schedule-editor-rules)
10. [Local Arrangement Editor Rules](#10-local-arrangement-editor-rules)
11. [Constraint System](#11-constraint-system)
12. [Role Visibility Matrix](#12-role-visibility-matrix)
13. [Database Schema](#13-database-schema)
14. [API Endpoints Reference](#14-api-endpoints-reference)
15. [Version History UI](#15-version-history-ui)
16. [Delete & Reset Procedures](#16-delete--reset-procedures)
17. [Appendix: Key Design Decisions](#17-appendix-key-design-decisions)

---

## 1. Overview

The PUPLC Intelligent Class Scheduling System automates and manages academic class schedules using a **Case-Based Reasoning (CBR) + Random Forest** AI engine for generation and a **Constraint Satisfaction Problem (CSP)** engine for real-time conflict detection.

The system uses **two separate editors** with clearly defined purposes:
- **Final Schedule Editor** — for building and approving the official schedule that appears in SIS.
- **Local Arrangement Editor** — for managing temporary, day-to-day adjustments on top of the approved schedule.

Schedules go through a **3-status lifecycle** (Draft → Approved → Locked), with an **Adjustment Period** window (Final Schedule only) during which an Approved schedule can still be corrected before it becomes fully locked.

**Group key** = `(programcode, yearlevel, acadyear, term)`  
Every version rule applies within this group scope.

---

## 2. The Two Editors

### Final Schedule Editor
- **Purpose:** Build and approve the official class schedule (uploaded to SIS).
- **Buttons:** `Save as Draft` · `Approve Schedule`
- **Access:** Always available to Academic Head.
- **Editable until:** Adjustment Period expires → then Locked.

### Local Arrangement Editor
- **Purpose:** Manage temporary, date-specific changes on top of the official schedule (room swaps, cancellations, makeups). Does NOT modify the official schedule.
- **Buttons:** `Save as Draft` (Pending) · `Save Locally` (Active)
- **Access:** Only unlocked after an **Approved** schedule exists for that group.
- **Editable until:** Semester ends — completely independent of the Adjustment Period.

> **Access Gate:** Local Arrangement Editor is locked/hidden if no Approved schedule exists yet. You must approve a Final Schedule first.

> **Key Rule:** Even if the Final Schedule is Locked (Adjustment Period expired), the Local Arrangement Editor remains fully editable until the semester ends.

---

## 3. Status Definitions

### Final Schedule — Version Statuses

| Status | Meaning | Editable? | Who Sees It |
|---|---|---|---|
| **Draft** | Work-in-progress. Not official yet. | Yes, freely | Academic Head only |
| **Approved** | Official schedule. Live in SIS. | Yes, during Adjustment Period only | All roles |
| **Locked** | Adjustment Period expired. Fully frozen. | No | All roles |
| **Archive** | Superseded by a newer Approved version. | No (restorable to Draft) | Academic Head only |

### Local Arrangement — Entry Statuses

| Status | Meaning | In Effect? | Editable? |
|---|---|---|---|
| **Pending** | Saved as Draft — being prepared, not yet applied. | No | Yes, until semester ends |
| **Active** | Saved Locally — confirmed and currently in effect. | Yes | Yes, until semester ends |
| **Expired** | Semester has ended. Entry archived automatically. | No | No |

> Local Arrangement statuses are **completely independent of the Adjustment Period**. Entries remain editable as long as the semester is ongoing, regardless of whether the Final Schedule is Approved or Locked.

---

## 4. Schedule Version Lifecycle

### Final Schedule
```
  [Save as Draft]      [Approve Schedule]    [Adj. Period Expires]
        │                     │                      │
        ▼                     ▼                      ▼
  ┌─────────┐          ┌───────────┐          ┌────────────┐
  │  DRAFT  │ ────────▶│ APPROVED  │─────────▶│   LOCKED   │
  └─────────┘          └───────────┘          └────────────┘
                              │                      │
                              └──────────────────────┘
                                        │ (replaced by new Approved)
                                        ▼
                                  ┌──────────┐
                                  │  ARCHIVE │ ←── Restore → new Draft
                                  └──────────┘
```

### Local Arrangement
```
  [Save as Draft]     [Save Locally]     [Semester Ends]
        │                   │                  │
        ▼                   ▼                  ▼
   PENDING ──────────▶  ACTIVE  ──────────▶ EXPIRED
```

### Independent Timelines
```
Approval date
     │
     ├── Adjustment Period (e.g. 7 days) ──────────────▶ Final Schedule LOCKED
     │
     └── Semester continues ──────────────────────────────────▶ Semester End
                                                                      │
                                          Local Arrangements auto → EXPIRED
```

---

## 5. Version State Rules

| Rule | Description |
|---|---|
| **Rule 1** | Only **1 Draft** may exist per group key. Saving overwrites the existing Draft — same version number, updated content. |
| **Rule 2** | Only **1 Approved** version may exist per group key. Approving archives the current Approved/Locked version first. |
| **Rule 3** | An Approved version is editable **only within the Adjustment Period**. After expiry it becomes Locked automatically. |
| **Rule 4** | A **Locked** version is fully immutable. To revise: Restore from Version History → new Draft. |
| **Rule 5** | **Restore** archives any existing Draft first, then creates a new Draft from the selected Archive. |
| **Rule 6** | `version_number` increments each time a new Draft is committed or a version is restored. |
| **Rule 7** | Local Arrangement entries are **independent of version status**. They overlay on the active Approved/Locked schedule and remain editable until the semester ends. |

---

## 6. Adjustment Period Setting

> **Applies to Final Schedule only. Has no effect on Local Arrangements.**

Configurable in **Settings → Schedule → Adjustment Period** (number of days).

| Scenario | Behavior |
|---|---|
| Period = 7 days, today is Day 3 | Approved schedule still editable in Final Schedule Editor |
| Period = 7 days, today is Day 8 | Final Schedule auto-becomes Locked |
| Period = 0 | Final Schedule locks immediately upon Approval |

The system checks `approved_at + adjustment_period_days < NOW()` at runtime. Status updated to `'Locked'` lazily on first access after expiry, or by a nightly cleanup job.

> Even during Adjustment Period, **Save as Draft** is available for bigger revisions — saves a Draft copy while the Approved schedule stays live.

---

## 7. Lifecycle Scenarios

### Scenario 1: First-Time Schedule Generation
1. Academic Head generates schedule via AI engine.
2. System creates `schedule` + `schedule_version` (status=**Draft**, v1).
3. Sessions saved to `schedule_sessions`.
4. Local Arrangement Editor is **not yet accessible**.
- **State:** 1 Draft (v1) | Local Arrangement: locked

### Scenario 2: Edit Draft
1. Opens Draft in Final Schedule Editor, makes changes.
2. **Save as Draft** — sessions updated in-place, version number unchanged.
- **State:** 1 Draft (v1, updated)

### Scenario 3: Approve the Schedule
1. Academic Head clicks **Approve Schedule**.
2. Existing Approved/Locked version (if any) → Archive.
3. Draft → **Approved**. Adjustment Period timer starts.
4. Schedule appears in SIS. Local Arrangement Editor now **unlocked**.
- **State:** 1 Approved (v1) | Local Arrangement: unlocked

### Scenario 4: Quick Fix During Adjustment Period
1. Spots an error within Adjustment Period.
2. Fixes it, clicks **Approve Schedule** → old Approved → Archive, updated → Approved.
- **State:** 1 Approved (v1 corrected), 1 Archive (v1 original)

### Scenario 5: Bigger Revision (Draft First)
1. **Save as Draft** → Draft v2 saved. Approved v1 stays live.
2. Reviews, edits. **Approve Schedule** → v2 Approved, v1 → Archive.
- **State:** 1 Approved (v2), 1 Archive (v1)

### Scenario 6: Adjustment Period Expires
1. System marks Approved → **Locked**. Final Schedule Editor read-only.
2. Local Arrangement Editor still fully accessible and editable.
3. To change Final Schedule: Restore from Version History → new Draft.
- **State:** 1 Locked (v2) | Local Arrangement: still active

### Scenario 7: Local Arrangement (Temporary Change)
1. Local Arrangement Editor accessible (Approved/Locked exists).
2. Academic Head enters a change (e.g., room swap for May 28).
3. **Save as Draft** → Pending. **Save Locally** → Active.
4. Active entry overlays on the official schedule for that date.
- **State:** Official schedule unchanged | Local arrangement: Active

### Scenario 8: Faculty Request → Local Arrangement
1. Faculty submits request via `schedule_change_request`.
2. Academic Head reviews request, opens Local Arrangement Editor.
3. Creates local arrangement entry linked to the request (`request_id`).
4. **Save Locally** → Active. Faculty request marked resolved.
- **State:** Faculty request fulfilled | Local arrangement: Active

### Scenario 9: Semester Ends
1. System checks semester end date.
2. All Pending and Active local arrangements → **Expired** automatically.
3. Final Schedule remains Locked (or Approved if still in adj. period).
- **State:** All local arrangements: Expired

### Scenario 10: Restore from Archive
1. Version History → select Archive → Restore.
2. Existing Draft → Archive. New Draft from selected Archive (new version_number).
3. Edit in Final Schedule Editor → Approve when ready.
- **State:** New Draft | Original Archive preserved

---

## 8. Auto-Generation Engine

### Algorithm: CBR + Random Forest

| Component | Role |
|---|---|
| **CBR** | Retrieves the most similar past schedule as the base template. |
| **Random Forest** | Predicts optimal time slot assignments. |
| **CSP Post-Processing** | Verifies no Hard Constraint is violated after generation. |

### Generation Flow
```
Input → CBR Retrieval → Random Forest Prediction → CSP Verification
                                                          │
                              HC Violations → log to schedule_exception_log
                              No violations → save as Draft
```

### Hard Constraints

| HC | Description |
|---|---|
| HC-1 | No faculty double-booking |
| HC-2 | No room double-booking |
| HC-3 | No student group double-booking |
| HC-4 | Subject unit hours satisfied within the week |
| HC-5 | NSTP subjects: Sunday-only |
| HC-6 | Room capacity ≥ enrollment |

---

## 9. Final Schedule Editor Rules

| Rule | Description |
|---|---|
| **Buttons** | Save as Draft + Approve Schedule only |
| **Draft editing** | All session slots freely editable. Real-time CSP on each change. |
| **Approved editing** | Editable only during Adjustment Period. Read-only after expiry. |
| **isDraft check** | Locked/Archived versions open in read-only mode with a Restore banner. |
| **NSTP Rule** | NSTP subjects: Sunday-only. Hard block enforced. |
| **Progress Colors** | Red 0–49% · Amber 50–79% · Green 80–100% |
| **Room Ranking** | Ranked by: (1) capacity fit, (2) building proximity, (3) equipment |

---

## 10. Local Arrangement Editor Rules

| Rule | Description |
|---|---|
| **Access gate** | Hidden unless Approved or Locked schedule exists for the group |
| **Buttons** | Save as Draft (Pending) + Save Locally (Active) only |
| **Save as Draft** | Saves as Pending — not yet active. Still editable. |
| **Save Locally** | Saves as Active — immediately in effect for the specified date. |
| **Editable until** | Both Pending and Active entries remain editable until **semester ends** — independent of Adjustment Period |
| **Auto-expire** | When semester ends, all Pending and Active → Expired automatically |
| **Scope** | Targets a specific date. For recurring/permanent changes, use Final Schedule Editor. |
| **Overlay** | Active entries overlaid on official schedule for that date in all views. |
| **Types** | reschedule · cancellation · room_change · makeup |
| **Faculty requests** | Faculty requests from `schedule_change_request` feed into this editor. `request_id` links the two tables. |

---

## 11. Constraint System

### CSP Triggers

| Trigger | When |
|---|---|
| Auto-generation | After CBR + Random Forest |
| Final Schedule Editor — slot change | Real-time on each modification |
| Restore to Draft | After restoring sessions |

Violations logged to `schedule_exception_log` and surfaced as red warnings in the Final Schedule Editor.

---

## 12. Role Visibility Matrix

| Feature | Faculty | Academic Head | Admin |
|---|---|---|---|
| View Draft | ✗ | ✓ | ✗ |
| View Approved | ✓ | ✓ | ✓ |
| View Locked | ✓ | ✓ | ✓ |
| View Archive | ✗ | ✓ | ✗ |
| Final Schedule Editor | ✗ | ✓ | ✗ |
| Approve Schedule | ✗ | ✓ | ✗ |
| Local Arrangement Editor | ✗ | ✓ (if Approved/Locked exists) | ✗ |
| Restore from Archive | ✗ | ✓ | ✗ |
| Version History | ✗ | ✓ | ✗ |
| View Reports | ✓ (own) | ✓ (all) | ✓ (all) |

> Faculty see Approved or Locked as the official schedule. Active Local Arrangements overlay on their view for applicable dates.

---

## 13. Database Schema

### Updated CHECK Constraints

```sql
-- schedule_version
CONSTRAINT schedule_version_status_check
    CHECK (status IN ('Draft', 'Approved', 'Locked', 'Archive'))

-- local_schedule_adjustment
CONSTRAINT local_adj_status_check
    CHECK (status IN ('Pending', 'Active', 'Expired'))
```

### New Columns

```sql
-- Track approval time and lock time for Final Schedule
ALTER TABLE schedule_version
    ADD COLUMN approved_at TIMESTAMP,
    ADD COLUMN locked_at   TIMESTAMP;

-- Status and faculty request link for Local Arrangement
ALTER TABLE local_schedule_adjustment
    ADD COLUMN status     VARCHAR(10) DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Active', 'Expired')),
    ADD COLUMN request_id INT REFERENCES schedule_change_request(requestid);
```

### Cascade Behavior

```
schedule
  └── schedule_version (ON DELETE CASCADE)
        └── schedule_sessions (ON DELETE CASCADE)

local_schedule_adjustment  ── references schedule (NO CASCADE)
schedule_change_request    ── references schedule (NO CASCADE)
class_meeting_request      ── references schedule (NO CASCADE)
```

### Auto-Expire Logic (runtime or nightly job)

```sql
-- Expire local arrangements when semester ends
UPDATE local_schedule_adjustment la
JOIN schedule s ON la.scheduleid = s.scheduleid
SET la.status = 'Expired'
WHERE la.status IN ('Pending', 'Active')
  AND s.semester_end_date < CURRENT_DATE;
```

---

## 14. API Endpoints Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/schedule/versions` | All versions |
| GET | `/api/schedule/drafts` | Draft versions only |
| GET | `/api/schedule/load-draft/<versionid>` | Load sessions for a Draft |
| POST | `/api/schedule/save-draft` | Save/overwrite Draft |
| POST | `/api/schedule/approve` | Approve Draft → Approved; archive old |
| POST | `/api/schedule/versions/<id>/restore` | Restore Archive → new Draft |
| DELETE | `/api/schedule/drafts/<versionid>` | Delete a Draft |
| GET | `/api/local-arrangement/<group>` | Get all local arrangements for a group |
| POST | `/api/local-arrangement/save-draft` | Save as Pending |
| POST | `/api/local-arrangement/save-local` | Save as Active |
| PATCH | `/api/local-arrangement/<id>` | Update a Pending entry |
| DELETE | `/api/local-arrangement/<id>` | Delete a local arrangement entry |
| GET | `/api/settings/adjustment-period` | Get adjustment period (days) |
| POST | `/api/settings/adjustment-period` | Update adjustment period setting |

---

## 15. Version History UI

**Page:** `/schedule/version-history`

Displays all Final Schedule versions grouped by **Program → Semester → Year Level**.

**Pill color coding:**
- Amber = Draft
- Green = Approved
- Blue = Locked
- Gray = Archive (with Restore button)

**Nav:** Schedule submenu → Version History (below Drafts)

---

## 16. Delete & Reset Procedures

```sql
-- Delete order (respect FK constraints)
DELETE FROM local_schedule_adjustment;
DELETE FROM schedule_change_request;
DELETE FROM class_meeting_request;
DELETE FROM schedule_exception_log;
DELETE FROM schedule_sessions;
DELETE FROM schedule_version;
DELETE FROM schedule;

-- Reset sequences
ALTER TABLE schedule               ALTER COLUMN scheduleid       RESTART WITH 1;
ALTER TABLE schedule_version       ALTER COLUMN versionid        RESTART WITH 1;
ALTER TABLE schedule_sessions      ALTER COLUMN sessionid        RESTART WITH 1;
ALTER TABLE local_schedule_adjustment ALTER COLUMN adjustmentid  RESTART WITH 1;
ALTER TABLE schedule_change_request   ALTER COLUMN requestid     RESTART WITH 1;
ALTER TABLE class_meeting_request     ALTER COLUMN meetingrequestid RESTART WITH 1;
ALTER TABLE schedule_exception_log    ALTER COLUMN exceptionid   RESTART WITH 1;
```

---

## 17. Appendix: Key Design Decisions

| Decision | Rationale |
|---|---|
| 2 separate editors | Final Schedule and Local Arrangement serve different purposes. One merged editor caused confusion about what each button does. |
| Removed Published status | Draft → Approved is simpler when the Academic Head is the sole approver. |
| Adjustment Period — Final Schedule only | The adj. period is about correcting the official record before it's locked. Local arrangements are operational (day-to-day) and need to stay flexible throughout the semester. |
| Local Arrangement editable until semester ends | Faculty can request room changes, cancellations, and makeups at any point during the semester. Locking them to the adj. period would break this workflow. |
| Local Arrangement Pending/Active/Expired | Pending = drafting before it goes live. Active = confirmed. Expired = auto-archived at semester end. |
| Faculty requests feed into Local Arrangement | `schedule_change_request` is the input (faculty side); `local_schedule_adjustment` is the output (Academic Head action). Linked via `request_id`. |
| Archive is restorable | Safety net for reverting bad approvals. |
| CSP on every Final Schedule Editor save | Prevents approving schedules with HC violations. |

---

*AcadSDB v8.2 — Updated: Adjustment Period applies to Final Schedule only; Local Arrangement editable until semester ends.*  
*Prepared for PUPLC Intelligent Class Scheduling System — CAPSTONE Project 2026.*
