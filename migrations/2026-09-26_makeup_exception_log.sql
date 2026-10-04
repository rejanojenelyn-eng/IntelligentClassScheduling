-- 2026-09-26 — Make-up classes as date-specific schedule exceptions
--
-- An approved make-up class is a ONE-TIME meeting. It is recorded as one
-- schedule_exception_log row (source_type = 'makeup_class') that carries the
-- exact date, time and room, instead of being inserted into schedule_sessions
-- (the recurring weekly grid).
--
-- Additive and idempotent. The app also applies this automatically at runtime
-- (makeup_requests.ENSURE_SCHEMA_SQL via _ensure_request_tables()).

BEGIN;

ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS exception_date DATE;
ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS starttimeid    INTEGER;
ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS endtimeid      INTEGER;
ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS roomid         INTEGER;
ALTER TABLE public.schedule_exception_log ADD COLUMN IF NOT EXISTS employeenumber VARCHAR(30);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_starttime') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_starttime FOREIGN KEY (starttimeid) REFERENCES public.timeslot (timeid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_endtime') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_endtime FOREIGN KEY (endtimeid) REFERENCES public.timeslot (timeid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_room') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_room FOREIGN KEY (roomid) REFERENCES public.room (roomid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_sel_faculty') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT fk_sel_faculty FOREIGN KEY (employeenumber) REFERENCES public.faculty (employeenumber);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_sel_makeup_complete') THEN
        ALTER TABLE public.schedule_exception_log
            ADD CONSTRAINT chk_sel_makeup_complete CHECK (
                source_type <> 'makeup_class' OR (
                    source_requestid IS NOT NULL AND exception_date IS NOT NULL
                    AND starttimeid IS NOT NULL AND endtimeid IS NOT NULL
                    AND starttimeid <> endtimeid));
    END IF;
END $$;

-- At most one exception per originating request (blocks duplicate approvals).
CREATE UNIQUE INDEX IF NOT EXISTS uq_sel_source_request
    ON public.schedule_exception_log (source_type, source_requestid)
    WHERE source_requestid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sel_exception_date
    ON public.schedule_exception_log (exception_date)
    WHERE exception_date IS NOT NULL;

COMMIT;

-- Cleanup for databases that ran the OLD approval code (which inserted the
-- make-up into schedule_sessions as a weekly slot). ASDBv11 had 0 approved
-- make-ups on 2026-09-26, so nothing needed removing there. On another copy,
-- review the rows this finds before deleting anything by hand:
--
--   SELECT cmr.requestid, ss.sessionid, ss.versionid, ss.daydesc
--   FROM class_meeting_request cmr
--   JOIN schedule_version sv ON sv.scheduleid = cmr.scheduleid AND sv.status = 'Published'
--   JOIN schedule_sessions ss ON ss.versionid = sv.versionid
--        AND ss.daydesc = TO_CHAR(cmr.requested_date, 'FMDay')
--        AND ss.starttimeid = cmr.new_starttimeid AND ss.endtimeid = cmr.new_endtimeid
--        AND ss.roomid IS NOT DISTINCT FROM cmr.new_roomid
--   WHERE cmr.status = 'Approved';
