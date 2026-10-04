-- 2026-09-28 — Self-service password change: once per month + emailed verification code
--
-- Adds columns to the existing accounts table (no new table: an account only
-- ever has one password-change verification in progress). Additive and
-- idempotent. The app also applies this at startup (sm_password_change_otp).

BEGIN;

-- Set on every successful password change (Profile Settings and the
-- first-login setup wizard). The next self-service change is allowed once
-- NOW() >= password_changed_at + INTERVAL '1 month'. NULL = never changed.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP NULL;

-- Where the verification code is emailed:
--   * Faculty / Academic Head accounts (employeenumber set) -> faculty.email
--   * Standalone accounts with no faculty record (the system Admin) -> accounts.email
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email VARCHAR(255) NULL;

-- Undo a superseded draft of this migration that linked the system Admin
-- account to a personal faculty record. The Admin must stay a standalone
-- account; the faculty record itself is not touched.
UPDATE accounts SET employeenumber = NULL
WHERE username = 'admin' AND role = 'Admin' AND employeenumber = '13242354';

-- Verification code state. The code itself is never stored — only its
-- scrypt hash — and the hash is cleared the moment it is used or expires.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_hash        VARCHAR(255) NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_expires_at  TIMESTAMP NULL;  -- sent_at + 10 min
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_sent_at     TIMESTAMP NULL;  -- 60 s resend cooldown
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_attempts    SMALLINT NOT NULL DEFAULT 0;  -- max 5
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_verified_at TIMESTAMP NULL;  -- code accepted; 10 min to save

COMMIT;
