-- 2026-10-01 — request cancellation + Local / request integrity (additive, idempotent)
--
-- Applied automatically at app start by _run_startup_migrations() (step
-- sm_request_cancel_and_local_integrity). Kept here as the reviewable record.
--
-- Pre-checks on the live DB before writing this (2026-10-01):
--   class_meeting_request / schedule_change_request rows: 0 / 0
--   schedule_change_request.official_sessionid: 0 NULL, 0 valid, 0 orphan
--   local_arrangement.status values: Published (6), Archived (1)
--   local_arrangement.semesterid: 0 NULL, 0 orphan (7 rows)

-- 10.1 Request cancellation. A faculty member may cancel their OWN Pending request
-- (DELETE /api/faculty/my_requests/<makeup|adjustment>/<id>), on both request tables.
-- A cancelled request was never reviewed, so it must carry no reviewer.
ALTER TABLE public.class_meeting_request DROP CONSTRAINT IF EXISTS chk_cmr_status;
ALTER TABLE public.class_meeting_request ADD CONSTRAINT chk_cmr_status
    CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled'));
ALTER TABLE public.class_meeting_request DROP CONSTRAINT IF EXISTS chk_cmr_review;
ALTER TABLE public.class_meeting_request ADD CONSTRAINT chk_cmr_review
    CHECK ((status IN ('Pending', 'Cancelled') AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status IN ('Approved', 'Rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL));

ALTER TABLE public.schedule_change_request DROP CONSTRAINT IF EXISTS chk_scr_status;
ALTER TABLE public.schedule_change_request ADD CONSTRAINT chk_scr_status
    CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled'));
ALTER TABLE public.schedule_change_request DROP CONSTRAINT IF EXISTS chk_scr_review;
ALTER TABLE public.schedule_change_request ADD CONSTRAINT chk_scr_review
    CHECK ((status IN ('Pending', 'Cancelled') AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status IN ('Approved', 'Rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL));

-- 10.2 The exact Official occurrence a Schedule Adjustment request targets.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_scr_official_session') THEN
        ALTER TABLE public.schedule_change_request
            ADD CONSTRAINT fk_scr_official_session FOREIGN KEY (official_sessionid)
            REFERENCES public.schedule_sessions(sessionid) NOT VALID;
    END IF;
END $$;
ALTER TABLE public.schedule_change_request VALIDATE CONSTRAINT fk_scr_official_session;

-- 10.4 Local hardening (existing data verified compatible).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_local_arrangement_status') THEN
        ALTER TABLE public.local_arrangement ADD CONSTRAINT chk_local_arrangement_status
            CHECK (status IN ('Draft', 'Published', 'Archived')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_local_arrangement_semester') THEN
        ALTER TABLE public.local_arrangement ADD CONSTRAINT fk_local_arrangement_semester
            FOREIGN KEY (semesterid) REFERENCES public.semester(semesterid) NOT VALID;
    END IF;
END $$;
ALTER TABLE public.local_arrangement VALIDATE CONSTRAINT chk_local_arrangement_status;
ALTER TABLE public.local_arrangement VALIDATE CONSTRAINT fk_local_arrangement_semester;
