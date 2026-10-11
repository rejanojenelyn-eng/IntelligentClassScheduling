-- Self-service email change (Profile Settings > Email): a 6-digit code is sent
-- to the NEW address and must be entered before the address is saved.
-- Stored only as a hash, together with the address it was sent to.
-- app.py applies this automatically on startup (step sm_email_change_otp);
-- this file is for databases set up by hand. Safe to run more than once.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_hash       VARCHAR(255) NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_new        VARCHAR(255) NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_expires_at TIMESTAMP NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_sent_at    TIMESTAMP NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_attempts   SMALLINT NOT NULL DEFAULT 0;
