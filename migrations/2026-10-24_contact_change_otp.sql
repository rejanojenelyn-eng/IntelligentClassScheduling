-- Account Setup Step 4 and Profile Settings > Contact Number: a number is saved only after
-- a 6-digit code, emailed to the account's REGISTERED address, is entered (contact_change.py).
-- The code is stored only as a hash, together with the number it was sent for.
-- contact_otp_window_* limit how many codes can be requested per hour.
--
-- email_verified_addr: the address the user proved they can read (by a code sent to it).
-- An email counts as verified only while accounts.email still equals it, so an address
-- the Admin typed in (or changed later) is never treated as verified. The Account Setup
-- Email step uses it to start empty for a new account instead of showing that address.
--
-- app.py applies this automatically on startup (steps sm_email_change_otp and
-- sm_contact_change_otp); this file is for databases set up by hand.
-- Additive and idempotent (safe to run more than once). Nothing existing is changed.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_verified_addr      VARCHAR(255) NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_hash         VARCHAR(255) NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_new          VARCHAR(20)  NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_expires_at   TIMESTAMP NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_sent_at      TIMESTAMP NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_attempts     SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_window_start TIMESTAMP NULL;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_window_sends SMALLINT NOT NULL DEFAULT 0;
-- The number the user proved with a code (Account Setup Step 4 or Profile Settings).
-- The saved number counts as verified only while it equals this, so a number the Admin
-- typed in never lets Account Setup's Save and Next through without a code.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_verified         VARCHAR(20)  NULL;
