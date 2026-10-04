-- 2026-09-28 — Profile pictures (and last login) as part of the accounts schema
--
-- The picture itself is stored as a FILE in static/uploads/profile_photos/
-- (the project's existing file storage). accounts.profile_photo keeps only its
-- path, e.g. /static/uploads/profile_photos/profile_12079_3f9a1c2b7d4e.png,
-- on the owner's own account row — a faculty member's account is linked to
-- their faculty record through accounts.employeenumber.
--
-- These columns used to be created lazily by ALTERs inside request handlers;
-- this makes them part of the schema. Additive and idempotent. The app also
-- applies it at startup (sm_account_profile_photo).

BEGIN;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_login    TIMESTAMP;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS profile_photo VARCHAR(255);
COMMIT;
