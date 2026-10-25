-- Account Setup Step 5 edits are free (no one-time limit) and are logged separately.
--
-- 1. faculty_field_change_log (from 2026-10-22_one_time_employment_edit.sql) is created if
--    it is missing. Without it every Step 5 field showed READ-ONLY: the app treats a missing
--    log as "self-editing not available yet".
-- 2. A third change_source, 'setup_edit', records changes made during Account Setup. Only
--    'self_edit' rows (Profile Settings, after setup) use up the one-time allowance for
--    Specialization / Employment Type / Employment Status (ux_ffcl_one_self_edit), so setup
--    edits never consume it, and an allowance already used is never restored.
--
-- app.py applies this automatically on startup (step sm_faculty_field_change_log); this file
-- is for databases set up by hand. Idempotent; nothing existing is deleted or rewritten.

CREATE TABLE IF NOT EXISTS public.faculty_field_change_log (
    changeid        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employeenumber  VARCHAR(30)  NOT NULL,
    field_name      VARCHAR(30)  NOT NULL,
    old_value       VARCHAR(255),
    new_value       VARCHAR(255),
    change_source   VARCHAR(20)  NOT NULL,
    changed_by      VARCHAR(50)  NOT NULL,
    changed_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    requestid       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ffcl_employee_time
    ON public.faculty_field_change_log (employeenumber, changed_at);

CREATE UNIQUE INDEX IF NOT EXISTS ux_ffcl_one_self_edit
    ON public.faculty_field_change_log (employeenumber, field_name)
    WHERE change_source = 'self_edit'
      AND field_name IN ('Specialization', 'Employment Type', 'Employment Status');

ALTER TABLE public.faculty_field_change_log DROP CONSTRAINT IF EXISTS chk_ffcl_change_source;
ALTER TABLE public.faculty_field_change_log ADD CONSTRAINT chk_ffcl_change_source
    CHECK (change_source IN ('self_edit', 'setup_edit', 'admin_correction'));
