-- Login protection (see login_security.py): 5 consecutive wrong passwords lock
-- password login for 5 minutes; failed attempts are also limited per IP address.
-- The counter is keyed by a SHA-256 of the lower-cased username typed (existing
-- account or not), so nothing typed at login is stored in plain text.
-- app.py applies this automatically on startup (step sm_login_lockout);
-- this file is for databases set up by hand. Safe to run more than once.
CREATE TABLE IF NOT EXISTS login_throttle (
    username_key    CHAR(64)  PRIMARY KEY,
    failed_count    SMALLINT  NOT NULL DEFAULT 0,
    last_failed_at  TIMESTAMP NULL,
    locked_at       TIMESTAMP NULL,
    locked_until    TIMESTAMP NULL
);

CREATE TABLE IF NOT EXISTS login_ip_failures (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ip         VARCHAR(64) NOT NULL,
    failed_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_login_ip_failures_ip_at ON login_ip_failures (ip, failed_at);
