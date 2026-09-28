-- ============================================================================
-- PHASE 1 — LOCAL SCHEDULER SECTION-AWARE FOUNDATION
-- Date: 2026-09-27
--
-- Purpose:
--   Make Local Scheduler persistence section-aware without deleting or guessing
--   legacy data.
--
-- This migration:
--   1. Adds sectionid to local_arrangement.
--   2. Adds sectionid to local_displaced_subjects.
--   3. Safely backfills sectionid ONLY when the arrangement context resolves
--      to exactly one section.
--   4. Adds foreign keys and lookup indexes.
--   5. Replaces the old displacement uniqueness rule with a section-aware rule.
--
-- IMPORTANT:
--   Legacy rows from a Program + Year Level that has multiple sections cannot
--   be assigned to a section reliably because the old Local Scheduler schema
--   never stored section identity. Those rows intentionally remain NULL and
--   must not be guessed.
--
--   Phase 1 backend changes will require sectionid for NEW Local Scheduler
--   saves. Keeping this column nullable during migration protects existing
--   databases from destructive/incorrect backfills.
-- ============================================================================

BEGIN;

-- --------------------------------------------------------------------------
-- 1. Add section identity to Local Arrangement headers.
-- --------------------------------------------------------------------------
ALTER TABLE public.local_arrangement
    ADD COLUMN IF NOT EXISTS sectionid INTEGER;

-- --------------------------------------------------------------------------
-- 2. Add section identity to displaced-subject records.
--    Displacement must be scoped to a section; otherwise moving a subject in
--    one section can incorrectly suppress the Official session of another.
-- --------------------------------------------------------------------------
ALTER TABLE public.local_displaced_subjects
    ADD COLUMN IF NOT EXISTS sectionid INTEGER;

-- --------------------------------------------------------------------------
-- 3. Safe legacy backfill for local_arrangement.
--
-- Resolve the arrangement's Academic Year through semester, then locate the
-- matching program_yearlevel. Backfill only when that exact context contains
-- ONE section. Multi-section contexts are deliberately left NULL.
-- --------------------------------------------------------------------------
WITH single_section_context AS (
    SELECT
        la.arrangementid,
        MIN(sec.sectionid) AS sectionid
    FROM public.local_arrangement la
    JOIN public.semester sem
      ON sem.semesterid = la.semesterid
    JOIN public.program_yearlevel pyl
      ON UPPER(pyl.programcode) = UPPER(la.programcode)
     AND pyl.yearlevel = la.yearlevel
     AND pyl.academicyearid = sem.academicyearid
    JOIN public.sections sec
      ON sec.programyearlevelid = pyl.programyearlevelid
    WHERE la.sectionid IS NULL
    GROUP BY la.arrangementid
    HAVING COUNT(DISTINCT sec.sectionid) = 1
)
UPDATE public.local_arrangement la
SET sectionid = ssc.sectionid
FROM single_section_context ssc
WHERE la.arrangementid = ssc.arrangementid
  AND la.sectionid IS NULL;

-- --------------------------------------------------------------------------
-- 4. Backfill displacement rows from their owning arrangement when possible.
-- --------------------------------------------------------------------------
UPDATE public.local_displaced_subjects lds
SET sectionid = la.sectionid
FROM public.local_arrangement la
WHERE lds.displaced_by = la.arrangementid
  AND lds.sectionid IS NULL
  AND la.sectionid IS NOT NULL;

-- --------------------------------------------------------------------------
-- 5. Foreign keys.
--    Guarded so this migration remains idempotent.
-- --------------------------------------------------------------------------
DO $$
BEGIN
    ALTER TABLE public.local_arrangement
        ADD CONSTRAINT local_arrangement_sectionid_fkey
        FOREIGN KEY (sectionid)
        REFERENCES public.sections(sectionid);
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE public.local_displaced_subjects
        ADD CONSTRAINT local_displaced_subjects_sectionid_fkey
        FOREIGN KEY (sectionid)
        REFERENCES public.sections(sectionid);
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
END $$;

-- --------------------------------------------------------------------------
-- 6. Replace the old displacement uniqueness rule.
--
-- OLD:
--   program + year + semester + subject
--
-- NEW:
--   program + year + section + semester + subject
--
-- PostgreSQL allows multiple NULL values in a UNIQUE constraint, which is
-- intentional here for unresolved legacy records.
-- --------------------------------------------------------------------------
ALTER TABLE public.local_displaced_subjects
    DROP CONSTRAINT IF EXISTS
    local_displaced_subjects_programcode_yearlevel_semesterid_s_key;

-- Runtime-created databases may have PostgreSQL's automatically generated
-- constraint name instead of the catch-up migration's explicit name.
ALTER TABLE public.local_displaced_subjects
    DROP CONSTRAINT IF EXISTS
    local_displaced_subjects_programcode_yearlevel_semesterid_subjectcode_key;

DO $$
BEGIN
    ALTER TABLE public.local_displaced_subjects
        ADD CONSTRAINT local_displaced_subjects_section_scope_key
        UNIQUE (programcode, yearlevel, sectionid, semesterid, subjectcode);
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table THEN NULL;
END $$;

-- --------------------------------------------------------------------------
-- 7. Indexes used by Local Scheduler retrieval/publish/conflict queries.
-- --------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_local_arrangement_section_context
    ON public.local_arrangement
       (programcode, yearlevel, sectionid, semesterid, status, is_active);

CREATE INDEX IF NOT EXISTS idx_local_displaced_section_context
    ON public.local_displaced_subjects
       (programcode, yearlevel, sectionid, semesterid, subjectcode, is_active);

COMMIT;

-- --------------------------------------------------------------------------
-- Optional verification queries after running the migration:
--
-- SELECT arrangementid, programcode, yearlevel, sectionid, semesterid, status
-- FROM public.local_arrangement
-- ORDER BY arrangementid;
--
-- SELECT arrangementid, programcode, yearlevel, semesterid
-- FROM public.local_arrangement
-- WHERE sectionid IS NULL;
--
-- Rows returned by the second query are legacy arrangements whose old schema
-- did not contain enough information to identify a section safely.
-- --------------------------------------------------------------------------
