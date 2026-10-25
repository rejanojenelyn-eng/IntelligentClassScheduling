-- One-time self-edit of employment information + audit trail of faculty record changes.
--
-- faculty_field_change_log keeps one row per changed faculty field:
--   change_source = 'self_edit'        the faculty member changed it (Account Setup Review
--                                      or Profile Settings)
--   change_source = 'admin_correction' an Admin approved a correction request
-- A self_edit row for Specialization / Employment Type / Employment Status IS the
-- one-time allowance being used: ux_ffcl_one_self_edit allows at most one per
-- employee and field, so two simultaneous requests can never both succeed.
-- Admin-approved corrections and password resets add no self_edit row, so they
-- never restore an allowance.
--
-- No foreign key to faculty: the log must outlive archived/removed faculty rows.
-- Additive and idempotent (safe to run more than once). Nothing existing is changed.

CREATE TABLE IF NOT EXISTS public.faculty_field_change_log (
    changeid        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employeenumber  VARCHAR(30)  NOT NULL,
    field_name      VARCHAR(30)  NOT NULL,
    old_value       VARCHAR(255),
    new_value       VARCHAR(255),
    change_source   VARCHAR(20)  NOT NULL,
    changed_by      VARCHAR(50)  NOT NULL,
    changed_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    requestid       INTEGER,     -- personal_info_correction_request.requestid for admin_correction
    CONSTRAINT chk_ffcl_change_source CHECK (change_source IN ('self_edit', 'admin_correction'))
);

CREATE INDEX IF NOT EXISTS idx_ffcl_employee_time
    ON public.faculty_field_change_log (employeenumber, changed_at);

CREATE UNIQUE INDEX IF NOT EXISTS ux_ffcl_one_self_edit
    ON public.faculty_field_change_log (employeenumber, field_name)
    WHERE change_source = 'self_edit'
      AND field_name IN ('Specialization', 'Employment Type', 'Employment Status');

-- At most one Pending correction request per user and field (the app already checks
-- this; the index closes the double-submit race). Skipped, with a notice, if duplicates
-- already exist -- resolve those first, then run this file again.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.personal_info_correction_request
               WHERE status = 'Pending'
               GROUP BY username, field_name HAVING COUNT(*) > 1) THEN
        RAISE NOTICE 'ux_correction_one_pending_per_field not created: duplicate Pending requests exist';
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS ux_correction_one_pending_per_field
            ON public.personal_info_correction_request (username, field_name)
            WHERE status = 'Pending';
    END IF;
END $$;
