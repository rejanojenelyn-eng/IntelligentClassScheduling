-- Forgot Password (logged out) — one row per code sent (or decoy request).
-- Applied automatically at app start (_run_startup_migrations →
-- password_reset.ENSURE_SCHEMA_SQL); safe to run by hand on Neon too.
-- Codes are stored only as hashes (code_hash); the post-verification reset
-- token only as a SHA-256 (reset_token_hash). userid is NULL for requests that
-- matched no eligible account (they can never verify).

CREATE TABLE IF NOT EXISTS password_reset_requests (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    flow_id          VARCHAR(32)  NOT NULL,           -- one Forgot Password attempt (request + resends)
    userid           INTEGER      NULL REFERENCES accounts(userid) ON DELETE CASCADE,
    employeenumber   VARCHAR(50)  NULL,
    code_hash        VARCHAR(255) NULL,                -- NULL once used / invalidated
    attempts         SMALLINT     NOT NULL DEFAULT 0,  -- wrong codes entered
    created_at       TIMESTAMP    NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMP    NOT NULL,            -- created_at + 10 minutes
    verified_at      TIMESTAMP    NULL,                -- code accepted (10-minute reset window)
    reset_token_hash VARCHAR(64)  NULL,
    used_at          TIMESTAMP    NULL,                -- password actually reset
    invalidated_at   TIMESTAMP    NULL,                -- superseded / too many attempts / admin reset
    request_ip       VARCHAR(64)  NULL
);
CREATE INDEX IF NOT EXISTS ix_prr_user_created ON password_reset_requests (userid, created_at);
CREATE INDEX IF NOT EXISTS ix_prr_ip_created   ON password_reset_requests (request_ip, created_at);
CREATE INDEX IF NOT EXISTS ix_prr_flow         ON password_reset_requests (flow_id, created_at);
