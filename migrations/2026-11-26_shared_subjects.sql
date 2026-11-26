-- 2026-11-26  Shared Subjects (Admin Settings > Merging & Calendar > Shared Subjects)
-- Additive and idempotent; also applied at startup by app._run_startup_migrations
-- (step sm_shared_subjects, from shared_subjects.TABLE_DDL).
--
-- A shared subject may be split between up to max_faculty faculty members per section.
-- Each slice (schedule_sessions row) may carry its own faculty; NULL = the subject's
-- faculty (schedule_version.employeenumber, then schedule.employeenumber), so every
-- existing row reads exactly as before.

CREATE TABLE IF NOT EXISTS public.shared_subject_policy (
    subjectcode  VARCHAR(30) PRIMARY KEY,
    max_faculty  SMALLINT NOT NULL CHECK (max_faculty BETWEEN 2 AND 10),
    updated_by   VARCHAR(100),
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE public.schedule_sessions
    ADD COLUMN IF NOT EXISTS employeenumber VARCHAR(30)
    REFERENCES public.faculty(employeenumber) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_session_faculty ON public.schedule_sessions (employeenumber);
