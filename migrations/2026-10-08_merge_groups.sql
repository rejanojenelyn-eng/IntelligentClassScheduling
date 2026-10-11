-- 2026-10-08 — HC16 Merge Groups (P1: configuration only; additive, idempotent)
--
-- Applied automatically at app start by _run_startup_migrations() (step
-- sm_merge_groups) and on demand by merge_groups.ensure_schema(). Kept here as the
-- reviewable record; the code executes THIS file so the two can never drift apart.
--
-- What it adds: the administrator-authored Merge Group configuration that will
-- replace the legacy pair/scope model (hc_merge_scope, hc_merge_scope_subjects,
-- hc_merge_section_pairs). Nothing here is read by scheduling until the internal
-- hc_merge_model switch is moved from 'legacy' to 'groups' (a later phase).
--
-- What it does NOT touch: mergedclass / mergedclass_sections stay the derived,
-- Published-only tables rebuilt by _sync_mergedclass_for_semester; schedule,
-- schedule_version and schedule_sessions get no new columns (a merged event's
-- identity is (mergegroupid, mergegroupmeetingid), resolved against the stored
-- group meetings — session IDs change on every Save/Publish).
--
-- Rollback: these tables are inert under hc_merge_model='legacy'. Dropping them
-- (merge_group_meeting, merge_group_member, merge_group, in that order) restores
-- the previous schema exactly.

-- One merged class definition for one semester (the semester implies the AY).
CREATE TABLE IF NOT EXISTS public.merge_group (
    mergegroupid            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    groupname               VARCHAR(100) NOT NULL,
    semesterid              INTEGER NOT NULL REFERENCES public.semester(semesterid),
    -- The subject the group is defined around; every member's own subject must be
    -- validated equivalent to it (merge_group_member.equivalence_basis).
    ref_curriculumsubjectid INTEGER NOT NULL REFERENCES public.curriculumsubject(curriculumsubjectid),
    faculty_mode            VARCHAR(20) NOT NULL,
    -- Designated faculty (SAME_FACULTY only, optional). NULL = TBA until deliberately set.
    employeenumber          VARCHAR(30) REFERENCES public.faculty(employeenumber),
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    origin                  VARCHAR(20) NOT NULL DEFAULT 'admin',
    created_by              VARCHAR(100),
    created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by              VARCHAR(100),
    updated_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_mg_faculty_mode CHECK (faculty_mode IN ('SAME_FACULTY', 'MULTIPLE_FACULTY')),
    CONSTRAINT chk_mg_origin CHECK (origin IN ('admin', 'migrated', 'editor')),
    CONSTRAINT chk_mg_designee_same_only CHECK (faculty_mode = 'SAME_FACULTY' OR employeenumber IS NULL),
    CONSTRAINT uq_mg_name UNIQUE (semesterid, groupname)
);

-- A participating section and ITS OWN curriculum subject (curricula differ per
-- program/year, so the member subject is stored per member, never as one code).
CREATE TABLE IF NOT EXISTS public.merge_group_member (
    mergegroupmemberid  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mergegroupid        INTEGER NOT NULL REFERENCES public.merge_group(mergegroupid) ON DELETE CASCADE,
    sectionid           INTEGER NOT NULL REFERENCES public.sections(sectionid),
    curriculumsubjectid INTEGER NOT NULL REFERENCES public.curriculumsubject(curriculumsubjectid),
    equivalence_basis   VARCHAR(30) NOT NULL,
    -- admin_override only: optional note + who/when (audited, shown in Settings).
    equivalence_note    TEXT,
    override_by         VARCHAR(100),
    override_at         TIMESTAMP,
    -- Mirrors merge_group.is_active (kept in the same transaction) so the database itself
    -- can forbid a section+subject from belonging to two ACTIVE groups.
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT chk_mgm_basis CHECK (equivalence_basis IN
        ('reference', 'same_code', 'same_name_and_hours', 'admin_override')),
    CONSTRAINT chk_mgm_override_audit CHECK (
        equivalence_basis <> 'admin_override'
        OR (override_by IS NOT NULL AND override_at IS NOT NULL)),
    CONSTRAINT uq_mgm_section UNIQUE (mergegroupid, sectionid)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mgm_active_section_subject
    ON public.merge_group_member (sectionid, curriculumsubjectid) WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_mgm_group ON public.merge_group_member (mergegroupid);

-- The override note became optional: databases created with the earlier rule (note
-- required) get the audit-only CHECK. Idempotent — only replaced while the old rule exists.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conrelid = 'public.merge_group_member'::regclass
                 AND conname = 'chk_mgm_override_audit'
                 AND pg_get_constraintdef(oid) LIKE '%equivalence_note%') THEN
        ALTER TABLE public.merge_group_member DROP CONSTRAINT chk_mgm_override_audit;
        ALTER TABLE public.merge_group_member ADD CONSTRAINT chk_mgm_override_audit CHECK (
            equivalence_basis <> 'admin_override'
            OR (override_by IS NOT NULL AND override_at IS NOT NULL));
    END IF;
END $$;

-- P7: a merged class confirmed in the Manual Editor is recorded with origin 'editor'.
-- Databases created before P7 get the widened CHECK. Idempotent.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conrelid = 'public.merge_group'::regclass
                 AND conname = 'chk_mg_origin'
                 AND pg_get_constraintdef(oid) NOT LIKE '%editor%') THEN
        ALTER TABLE public.merge_group DROP CONSTRAINT chk_mg_origin;
        ALTER TABLE public.merge_group ADD CONSTRAINT chk_mg_origin
            CHECK (origin IN ('admin', 'migrated', 'editor'));
    END IF;
END $$;

-- The group's authoritative meeting(s). Optional: a group with no (or not enough)
-- meetings is CONFIGURED / UNSCHEDULED or INCOMPLETE, never freely generated.
-- roomid NULL = room TBA.
CREATE TABLE IF NOT EXISTS public.merge_group_meeting (
    mergegroupmeetingid INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mergegroupid        INTEGER NOT NULL REFERENCES public.merge_group(mergegroupid) ON DELETE CASCADE,
    class_type          VARCHAR(10) NOT NULL DEFAULT 'Lecture',
    daydesc             VARCHAR(10) NOT NULL,
    starttimeid         INTEGER NOT NULL REFERENCES public.timeslot(timeid),
    endtimeid           INTEGER NOT NULL REFERENCES public.timeslot(timeid),
    roomid              INTEGER REFERENCES public.room(roomid),
    CONSTRAINT chk_mgmt_class_type CHECK (class_type IN ('Lecture', 'Lab')),
    CONSTRAINT chk_mgmt_day CHECK (daydesc IN
        ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')),
    CONSTRAINT chk_mgmt_order CHECK (endtimeid > starttimeid),   -- same rule as schedule_sessions
    CONSTRAINT uq_mgmt_slot UNIQUE (mergegroupid, daydesc, starttimeid)
);
CREATE INDEX IF NOT EXISTS ix_mgmt_group ON public.merge_group_meeting (mergegroupid);
