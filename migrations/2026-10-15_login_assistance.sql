-- Login assistance requests: "Contact Admin" on the login page (see login_assistance.py).
-- A logged-out user's account reference (username or Employee ID), concern and optional
-- description, handled by the Admin in User Management > Account Assistance
-- (Pending / In Progress / Resolved, identity verification before recovery actions).
-- app.py applies this automatically on startup (step sm_login_assistance);
-- this file is for databases set up by hand. Safe to run more than once.
CREATE TABLE IF NOT EXISTS login_assistance_request (
        id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        reference            VARCHAR(20)  NULL UNIQUE,
        account_reference    VARCHAR(60)  NOT NULL,
        userid               INTEGER      NULL REFERENCES accounts(userid) ON DELETE SET NULL,
        concern              VARCHAR(40)  NOT NULL,
        description          TEXT         NULL,
        status               VARCHAR(20)  NOT NULL DEFAULT 'Pending',
        admin_remarks        TEXT         NULL,
        resolution           TEXT         NULL,
        identity_method      VARCHAR(60)  NULL,
        identity_notes       TEXT         NULL,
        identity_verified_by VARCHAR(50)  NULL,
        identity_verified_at TIMESTAMP    NULL,
        actions_taken        TEXT         NULL,
        handled_by           VARCHAR(50)  NULL,
        created_at           TIMESTAMP    NOT NULL DEFAULT NOW(),
        updated_at           TIMESTAMP    NULL,
        resolved_at          TIMESTAMP    NULL,
        request_ip           VARCHAR(64)  NULL
    );
CREATE INDEX IF NOT EXISTS ix_lar_status_created ON login_assistance_request (status, created_at);
CREATE INDEX IF NOT EXISTS ix_lar_ref_created ON login_assistance_request (LOWER(account_reference), created_at);
CREATE INDEX IF NOT EXISTS ix_lar_ip_created ON login_assistance_request (request_ip, created_at);
