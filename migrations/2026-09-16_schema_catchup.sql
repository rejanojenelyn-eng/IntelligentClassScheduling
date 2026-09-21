-- ============================================================================
-- SCHEMA CATCH-UP MIGRATION
--
-- Purpose: bring a database that was created from the old
-- "ASDBv10-1 (1).sql" schema dump up to parity with what the current
-- app.py/scheduler.py/faculty_load.py code actually expects. That dump
-- predates months of ad-hoc ALTER TABLE changes made directly against the
-- "main" dev database and never captured back into a schema file, so a
-- fresh DB built from it is missing several columns/tables and has a few
-- columns that are too narrow for real data (e.g. historical_data."Time"
-- VARCHAR(30) instead of VARCHAR(100) — the "value too long for type
-- character varying(30)" crash this migration exists to fix).
--
-- Safe to run against a database that ALREADY has real imported data:
--   - every ADD COLUMN uses IF NOT EXISTS
--   - every column widened here is only ever made WIDER, never narrower
--     (narrowing risks truncating/rejecting data that's already there)
--   - every CREATE TABLE / CREATE SEQUENCE uses IF NOT EXISTS
--   - every constraint add is wrapped so it's skipped if it already exists
-- Re-running this script a second time is harmless (everything no-ops).
--
-- Generated 2026-09-16 by diffing "ASDBv10-1 (1).sql" against the live
-- schema of the "ASDBv10" database on the main dev machine.
-- ============================================================================

BEGIN;

-- ── 0. THE ORIGINAL BUG: curriculum.curriculumversion ─────────────────────
-- The old dump defines this column as `SMALLINT NOT NULL DEFAULT 1` with a
-- CHECK (curriculumversion > 0). The live schema has it as a plain nullable
-- INTEGER with no default and no check — every curriculum row's version is
-- NULL, and Postgres treats NULL <> NULL for uniqueness, which is exactly
-- what lets a Regular and a Bridging curriculum coexist for the same
-- program+year under uq_curriculum UNIQUE(programcode, curriculumcode,
-- curriculumversion). On a database still using the old definition, EVERY
-- new curriculum defaults to version=1 (never NULL), so creating a second
-- (Bridging) curriculum for a program/year that already has one collides
-- with "duplicate key value violates unique constraint uq_curriculum" —
-- this is the very first error this migration was written in response to.
ALTER TABLE public.curriculum ALTER COLUMN curriculumversion DROP DEFAULT;
ALTER TABLE public.curriculum ALTER COLUMN curriculumversion DROP NOT NULL;
ALTER TABLE public.curriculum ALTER COLUMN curriculumversion TYPE INTEGER;
ALTER TABLE public.curriculum DROP CONSTRAINT IF EXISTS chk_curriculumversion;
-- Backfill: NULL out any curriculumversion the old DEFAULT 1 already wrote,
-- so rows created before this migration stop blocking a second curriculum
-- type for the same program+year too (matches every row on the live DB,
-- where this column has always been NULL — the app never sets it itself).
UPDATE public.curriculum SET curriculumversion = NULL WHERE curriculumversion IS NOT NULL;

-- A few other CHECK constraints from the old dump were also dropped on the
-- live schema at some point (most likely because real data legitimately
-- violates them — e.g. a Practicum/OJT subject with far more than 50
-- tuition hours). Drop them here too so importing that data doesn't fail.
ALTER TABLE public.curriculum        DROP CONSTRAINT IF EXISTS chk_curriculumcode_not_blank;
ALTER TABLE public.curriculum        DROP CONSTRAINT IF EXISTS chk_curriculumyear_not_blank;
ALTER TABLE public.curriculumsubject DROP CONSTRAINT IF EXISTS chk_subjectcode_not_blank;
ALTER TABLE public.curriculumsubject DROP CONSTRAINT IF EXISTS chk_subjectname_not_blank;
ALTER TABLE public.curriculumsubject DROP CONSTRAINT IF EXISTS chk_lecturehours;
ALTER TABLE public.curriculumsubject DROP CONSTRAINT IF EXISTS chk_laboratoryhours;
ALTER TABLE public.curriculumsubject DROP CONSTRAINT IF EXISTS chk_creditunits;
ALTER TABLE public.curriculumsubject DROP CONSTRAINT IF EXISTS chk_tuitionhours;

-- historical_data is an archival table fed by messy real-world SIS import
-- data (negative/out-of-range hour or unit values do turn up in source
-- files) — the live schema has zero CHECK constraints on it for exactly
-- that reason. Match that here so an import doesn't get rejected archiving
-- a row that's merely odd, not actually wrong.
ALTER TABLE public.historical_data DROP CONSTRAINT IF EXISTS chk_history_yearlevel;
ALTER TABLE public.historical_data DROP CONSTRAINT IF EXISTS chk_history_lecturehours;
ALTER TABLE public.historical_data DROP CONSTRAINT IF EXISTS chk_history_laboratoryhours;
ALTER TABLE public.historical_data DROP CONSTRAINT IF EXISTS chk_history_creditunits;

-- ── 1. WIDEN existing columns that are too narrow for real data ───────────
-- curriculum_view reads 3 of the columns being widened below, and Postgres
-- refuses ALTER COLUMN TYPE while a view depends on the column — so drop it
-- first and recreate it (byte-for-byte, same definition) afterward.

DROP VIEW IF EXISTS public.curriculum_view;

ALTER TABLE public.curriculumsubject   ALTER COLUMN corequisite    TYPE VARCHAR(255);
ALTER TABLE public.curriculumsubject   ALTER COLUMN prerequisite   TYPE VARCHAR(255);
ALTER TABLE public.curriculumsubject   ALTER COLUMN semester       TYPE VARCHAR(10);

CREATE VIEW public.curriculum_view AS
 SELECT c.curriculumcode,
    c.curriculumid,
    cs.curriculumsubjectid,
    cs.subjectcode,
    cs.prerequisite AS "Prerequisite",
    cs.corequisite AS "Co-requisite",
    cs.subjectname,
    cs.lecturehours,
    cs.laboratoryhours,
    cs.creditunits,
    cs.tuitionhours,
    (c.programcode::text || '-'::text) || cs.yearlevel::text AS programyearlevel,
    cs.yearlevel,
    cs.semester
   FROM curriculumsubject cs
     JOIN curriculum c ON cs.curriculumid = c.curriculumid;

ALTER TABLE public.sections            ALTER COLUMN sectionname    TYPE VARCHAR(100);
ALTER TABLE public.employeetype        ALTER COLUMN typename       TYPE VARCHAR(50);
ALTER TABLE public.designation         ALTER COLUMN designationname TYPE VARCHAR(100);
ALTER TABLE public.faculty             ALTER COLUMN employeestatus TYPE VARCHAR(20);
-- Faculty imports (CSV/XLSX/PDF/DOCX) legitimately have rows missing an email
-- or contact number — the app already inserts NULL for either when blank, but
-- the old dump defines both NOT NULL, so those imports failed at the DB level.
ALTER TABLE public.faculty             ALTER COLUMN email           DROP NOT NULL;
ALTER TABLE public.faculty             ALTER COLUMN contactnumber   DROP NOT NULL;
ALTER TABLE public.faculty_archive     ALTER COLUMN employeestatus TYPE VARCHAR(20);
ALTER TABLE public.building            ALTER COLUMN buildingname   TYPE VARCHAR(100);
ALTER TABLE public.room                ALTER COLUMN roomname       TYPE VARCHAR(50);
ALTER TABLE public.room                ALTER COLUMN roomtype       TYPE VARCHAR(20);
ALTER TABLE public.room                ALTER COLUMN roomdesc       TYPE VARCHAR(200);
ALTER TABLE public.schedule_version    ALTER COLUMN status         TYPE VARCHAR(20);
ALTER TABLE public.class_meeting_request   ALTER COLUMN status     TYPE VARCHAR(20);
ALTER TABLE public.schedule_change_request ALTER COLUMN status     TYPE VARCHAR(20);

-- historical_data: the exact columns behind the reported crash
ALTER TABLE public.historical_data ALTER COLUMN "Program" TYPE VARCHAR(20);
ALTER TABLE public.historical_data ALTER COLUMN "Room"    TYPE VARCHAR(100);
ALTER TABLE public.historical_data ALTER COLUMN "Day/s"   TYPE VARCHAR(50);
ALTER TABLE public.historical_data ALTER COLUMN "Time"    TYPE VARCHAR(100);

-- ── 2. ADD columns that later migrations added but this dump never captured ─

ALTER TABLE public.curriculum
    ADD COLUMN IF NOT EXISTS curriculumtype VARCHAR(10) NOT NULL DEFAULT 'Regular';

ALTER TABLE public.curriculumsubject
    ADD COLUMN IF NOT EXISTS isbridging BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.academicyear
    ADD COLUMN IF NOT EXISTS isfinalized BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'Upcoming';

ALTER TABLE public.program_yearlevel
    ADD COLUMN IF NOT EXISTS section_naming_format VARCHAR(30);

ALTER TABLE public.employeetype
    ADD COLUMN IF NOT EXISTS restrict_pt_hours BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE public.accounts
    ADD COLUMN IF NOT EXISTS last_login TIMESTAMP,
    ADD COLUMN IF NOT EXISTS profile_photo VARCHAR(255);

ALTER TABLE public.schedule_version
    ADD COLUMN IF NOT EXISTS original_status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'official';

ALTER TABLE public.historical_data
    ADD COLUMN IF NOT EXISTS employeenumber VARCHAR(50);

ALTER TABLE public.class_meeting_request
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS decided_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS notes TEXT,
    ADD COLUMN IF NOT EXISTS remarks TEXT;

ALTER TABLE public.schedule_change_request
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS decided_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS end_date DATE,
    ADD COLUMN IF NOT EXISTS remarks TEXT;

-- ── 3. CREATE tables that don't exist at all in the old dump ──────────────
-- (Several of these already self-create on first use via app.py's own
--  CREATE TABLE IF NOT EXISTS bootstrap code, so this section mostly just
--  gets them created up front instead of on first request.)

CREATE TABLE IF NOT EXISTS public.activity_log (
    logid        SERIAL PRIMARY KEY,
    logtime      TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    action       VARCHAR(100) NOT NULL,
    details      TEXT,
    initiated_by VARCHAR(100),
    category     VARCHAR(50) DEFAULT 'system',
    log_color    VARCHAR(20) DEFAULT 'gray'
);

CREATE TABLE IF NOT EXISTS public.local_arrangement (
    arrangementid    SERIAL PRIMARY KEY,
    description      VARCHAR(200),
    programcode      VARCHAR(20),
    yearlevel        INTEGER,
    semesterid       INTEGER,
    ref_versionid    INTEGER,
    has_hc_violation BOOLEAN DEFAULT FALSE,
    violated_rules   JSONB DEFAULT '[]'::jsonb,
    override_reason  TEXT,
    is_active        BOOLEAN DEFAULT TRUE,
    created_by       VARCHAR(100),
    created_at       TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    status           VARCHAR(20) DEFAULT 'Draft'
);

CREATE TABLE IF NOT EXISTS public.local_arrangement_sessions (
    sessionid              SERIAL PRIMARY KEY,
    arrangementid          INTEGER,
    subjectcode            VARCHAR(50),
    daydesc                VARCHAR(20),
    starttimeid            INTEGER,
    endtimeid              INTEGER,
    roomid                 INTEGER,
    faculty_employeenumber VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS public.local_displaced_subjects (
    id            SERIAL PRIMARY KEY,
    programcode   VARCHAR(20) NOT NULL,
    yearlevel     INTEGER NOT NULL,
    semesterid    INTEGER NOT NULL,
    subjectcode   VARCHAR(50) NOT NULL,
    displaced_by  INTEGER,
    is_active     BOOLEAN DEFAULT TRUE,
    displaced_at  TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.merge_load_policy (
    policyid     SERIAL PRIMARY KEY,
    subjectcode  VARCHAR(50) NOT NULL,
    min_sections INTEGER NOT NULL,
    max_sections INTEGER NOT NULL,
    created_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.program_name_history (
    id          SERIAL PRIMARY KEY,
    programcode VARCHAR(20) NOT NULL,
    old_name    VARCHAR(200) NOT NULL,
    new_name    VARCHAR(200) NOT NULL,
    changed_by  VARCHAR(100),
    changed_at  TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.scheduler_config (
    config_key   VARCHAR(60) PRIMARY KEY,
    config_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public.section_curriculum_lock (
    sectionid        INTEGER NOT NULL,
    semesterid       INTEGER NOT NULL,
    curriculum_mode  VARCHAR(10) NOT NULL DEFAULT 'regular',
    locked_at        TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    PRIMARY KEY (sectionid, semesterid)
);

CREATE TABLE IF NOT EXISTS public.subject_faculty_assignment (
    id             SERIAL PRIMARY KEY,
    programcode    VARCHAR(20) NOT NULL,
    yearlevel      SMALLINT NOT NULL,
    semesterid     INTEGER NOT NULL,
    subjectcode    VARCHAR(50) NOT NULL,
    employeenumber VARCHAR(50) NOT NULL,
    createdat      TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    sectionid      INTEGER
);

-- Indexes / constraints for the new tables (guarded so a second run of this
-- script, or a table that partially existed already, doesn't error out).
DO $$ BEGIN
    ALTER TABLE public.local_arrangement_sessions
        ADD CONSTRAINT local_arrangement_sessions_arrangementid_fkey
        FOREIGN KEY (arrangementid) REFERENCES public.local_arrangement(arrangementid);
EXCEPTION WHEN duplicate_object THEN NULL; WHEN duplicate_table THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.local_displaced_subjects
        ADD CONSTRAINT local_displaced_subjects_programcode_yearlevel_semesterid_s_key
        UNIQUE (programcode, yearlevel, semesterid, subjectcode);
EXCEPTION WHEN duplicate_object THEN NULL; WHEN duplicate_table THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.local_displaced_subjects
        ADD CONSTRAINT local_displaced_subjects_displaced_by_fkey
        FOREIGN KEY (displaced_by) REFERENCES public.local_arrangement(arrangementid);
EXCEPTION WHEN duplicate_object THEN NULL; WHEN duplicate_table THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE public.subject_faculty_assignment
        ADD CONSTRAINT subject_faculty_assignment_programcode_yearlevel_semesterid_key
        UNIQUE (programcode, yearlevel, semesterid, subjectcode);
EXCEPTION WHEN duplicate_object THEN NULL; WHEN duplicate_table THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_program_name_history_code_date
    ON public.program_name_history (programcode, changed_at);

-- ── uq_schedule_subject_section_semester + schedule_version.employeenumber ──
-- Same ad-hoc-drift story as the rest of this file: uq_schedule_subject_section_semester
-- (ONE logical schedule row per curriculumsubjectid+sectionid+semesterid) already existed
-- on the live dev database but was never captured in a tracked schema file — so the
-- Manual Scheduler's Draft/Publish flow (app.py's _insert_batch, which used to blindly
-- INSERT a fresh schedule row on every save) hit "duplicate key value violates unique
-- constraint uq_schedule_subject_section_semester" the moment an already-Published
-- schedule was edited a second time. The fix (see _find_or_create_schedule in app.py)
-- reuses the existing scheduleid across Draft/Published/Archive versions instead of
-- minting a new one each save, and needs this constraint to exist as the safety net.
--
-- Guarded in a DO block (not a plain ALTER) because unlike every other change in this
-- file, this one CAN legitimately fail if a database already accumulated duplicate
-- schedule rows for the same key from the very bug being fixed — that must not abort
-- the whole catch-up transaction. If you see the NOTICE below, find and consolidate
-- those duplicates by hand (keep the row with real schedule_version/schedule_sessions
-- history, re-point orphaned schedule_version rows at it, delete the extra schedule
-- row), then re-run this file.
DO $$ BEGIN
    ALTER TABLE public.schedule DROP CONSTRAINT IF EXISTS uq_schedule_subject_section_semester;
    ALTER TABLE public.schedule
        ADD CONSTRAINT uq_schedule_subject_section_semester
        UNIQUE (curriculumsubjectid, sectionid, semesterid);
EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'uq_schedule_subject_section_semester NOT added — duplicate (curriculumsubjectid, sectionid, semesterid) rows already exist in public.schedule. Resolve them manually, then re-run this migration.';
END $$;

-- Per-version faculty snapshot (nullable) — schedule.employeenumber is shared across
-- every version of a reused scheduleid, so a Draft's in-progress faculty edit needs
-- somewhere else to live that doesn't affect the current Published view.
ALTER TABLE public.schedule_version ADD COLUMN IF NOT EXISTS employeenumber VARCHAR(30);
DO $$ BEGIN
    ALTER TABLE public.schedule_version
        ADD CONSTRAINT schedule_version_employeenumber_fkey
        FOREIGN KEY (employeenumber) REFERENCES public.faculty(employeenumber);
EXCEPTION WHEN duplicate_object THEN NULL; WHEN duplicate_table THEN NULL; END $$;

COMMIT;

-- ============================================================================
-- After this runs, "SELECT column_name FROM information_schema.columns
-- WHERE table_name='curriculum' AND column_name='curriculumtype'" and the
-- same for historical_data / employeenumber should both return one row.
-- ============================================================================
