-- Faculty can cancel their own correction request while it is Pending
-- (Profile Settings > My Correction Requests > Cancel Request).
--
-- A cancelled request keeps its row (history / audit): status = 'Cancelled' and the time in
-- cancelled_at. It leaves every Pending list and count, and can no longer be approved or
-- rejected. ux_correction_one_pending_per_field only covers Pending rows, so the same
-- field can be requested again afterwards.
--
-- app.py applies this file on startup (step sm_correction_request_cancel); it is also safe
-- to run by hand. Idempotent; no existing request is changed.
ALTER TABLE public.personal_info_correction_request ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP;

DO $$
DECLARE c TEXT;
BEGIN
    -- Replace the status check (the original was unnamed, created inline) with one that
    -- also allows 'Cancelled'.
    FOR c IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'public.personal_info_correction_request'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) ILIKE '%status%'
    LOOP
        EXECUTE format('ALTER TABLE public.personal_info_correction_request DROP CONSTRAINT %I', c);
    END LOOP;
    ALTER TABLE public.personal_info_correction_request ADD CONSTRAINT chk_correction_status
        CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled'));
END $$;
