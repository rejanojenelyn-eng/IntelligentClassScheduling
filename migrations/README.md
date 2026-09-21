# migrations/

**As of 2026-09-16, app.py self-heals its own schema on every startup** —
`_run_startup_migrations()` near the bottom of `app.py` now applies everything
this folder used to require a hand-run script for (including moving
`curriculum.curriculumtype` to the ASDBv11 `'REGULAR'`/`'WITH_BRIDGING'`
convention). On a machine running the current `app.py`, just starting the
Flask app is enough — no manual SQL step needed. The files below are kept for
machines still running an older `app.py` from before this existed, and as a
record of what changed and why.

Before that, the schema had been evolved by running one-off `ALTER TABLE`
statements directly against the dev database with nothing capturing them
back into a shared file. That's how it drifted out of sync with the
schema-dump `.sql` files people keep in Downloads for setting up a new
machine (see the incident these files exist to fix, below). They're plain
SQL — run with `psql` or pgAdmin, no tooling involved.

## 2026-09-16_schema_catchup.sql

**What it's for:** if you set up a database on a new/different machine from an
older exported schema dump (anything named like `ASDBv10*.sql` from before
September 2026), your database is missing columns/tables and has a few columns
too narrow for real data — that's what caused imports to fail with errors like
`Import failed: ... value too long for type character varying(30)` and
`duplicate key value violates unique constraint "uq_curriculum"`.

Run this once against that database to bring it up to date:

```
psql -h <host> -U <user> -d <dbname> -f 2026-09-16_schema_catchup.sql
```

It's safe to run against a database that already has real data in it — every
change is additive (`ADD COLUMN IF NOT EXISTS`) or widening-only (a column is
never made narrower), and the whole script runs in one transaction, so a
failure partway through rolls back cleanly with nothing changed. It's also
safe to run more than once — re-running it is a no-op.

## ASDBv10_schema_reference_2026-09-16.sql

A full `pg_dump --schema-only` of the actual current schema (from the main
dev database), as of the date in the filename. If you need to build a
**brand-new** database from scratch, use this file instead of any older
`ASDBv10*.sql` dump sitting in Downloads — this one is complete and current.
Regenerate it (`pg_dump --schema-only --no-owner --no-privileges`) whenever
someone makes more ad-hoc schema changes, and replace this file so it doesn't
go stale again.
