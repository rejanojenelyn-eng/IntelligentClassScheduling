-- Account Setup keeps everything the user enters until Step 5's Save and Confirm.
--
-- One row per account while its setup is running (server-side; the browser only holds a
-- "password chosen" flag). Steps 1-5 write here, never to the account:
--   password_hash   the new password from Step 1, hashed (never stored in plain text)
--   photo           a newly uploaded photo (its file waits in static/uploads/profile_photos/)
--   photo_removed   the saved photo is to be removed
--   email           the email address verified by code in Step 3
--   contactnumber   the contact number verified by code in Step 4
--   faculty         Review edits: {faculty column: new value}, incl. a new employeenumber
-- Save and Confirm writes all of it to accounts / faculty in ONE transaction, sets
-- account_setup_complete and deletes the row. Leave Setup, logging out or logging in again
-- deletes the row (and a waiting photo file), so the account stays exactly as it was.
--
-- Replaces the short-lived account_setup_snapshot table (rollback approach).
-- app.py applies this automatically on startup (step sm_account_setup_draft); this file is
-- for databases set up by hand. Idempotent.
DROP TABLE IF EXISTS public.account_setup_snapshot;

CREATE TABLE IF NOT EXISTS public.account_setup_draft (
    userid           INTEGER PRIMARY KEY REFERENCES public.accounts(userid) ON DELETE CASCADE,
    username         VARCHAR(50)  NOT NULL,
    started_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    password_hash    VARCHAR(255),
    password_set_at  TIMESTAMP,
    photo            VARCHAR(255),
    photo_removed    BOOLEAN      NOT NULL DEFAULT FALSE,
    email            VARCHAR(255),
    contactnumber    VARCHAR(20),
    faculty          JSONB        NOT NULL DEFAULT '{}'::jsonb
);
