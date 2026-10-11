-- 2026-10-08 — HC17 policy identity: explicit policy -> Merge Group mappings (P3; additive, idempotent)
--
-- Applied automatically at app start by _run_startup_migrations() (step
-- sm_hc17_policy_groups) and on demand by merge_groups.ensure_hc17_schema(). The code
-- executes THIS file, so the reviewed record and the applied schema cannot drift.
--
-- Why: under the group merge model an HC17 (merged-class faculty load) policy must
-- apply to explicitly chosen Merge Groups — never to whatever subject codes happen to
-- start with "NSTP"/"OU". One policy may apply to many groups; a group may have several
-- policies only when their section-count ranges do not overlap (no ambiguity).
--
-- Legacy mode is unaffected: merge_load_policy keeps its columns and its legacy
-- subject-code matching (app._merge_policy_rules_for) until the legacy model is retired.
-- Existing policy rows (e.g. the live 'NSTP' 2-3 rule) are NOT mapped to anything here:
-- mappings are created only when an administrator confirms them.
--
-- Rollback: DROP TABLE public.merge_load_policy_group; DROP FUNCTION
-- public.merge_load_policy_group_guard(), public.merge_load_policy_range_guard();
-- ALTER TABLE public.merge_load_policy DROP COLUMN policyname.

-- The HC17 policy table normally comes from app._ensure_merge_load_policy_table; repeat
-- its exact definition so this migration also works on a database that never opened
-- the HC17 settings yet.
CREATE TABLE IF NOT EXISTS public.merge_load_policy (
    policyid     SERIAL PRIMARY KEY,
    subjectcode  VARCHAR(50)   NOT NULL,
    min_sections INTEGER       NOT NULL,
    max_sections INTEGER       NOT NULL,
    created_at   TIMESTAMP     DEFAULT NOW()
);

-- Optional administrator-facing name ("NSTP Shared Load"); subjectcode stays the legacy
-- matching key and a fallback label.
ALTER TABLE public.merge_load_policy ADD COLUMN IF NOT EXISTS policyname VARCHAR(100);

-- Explicit applicability: which Merge Groups a policy applies to (group model only).
CREATE TABLE IF NOT EXISTS public.merge_load_policy_group (
    policyid     INTEGER NOT NULL REFERENCES public.merge_load_policy(policyid) ON DELETE CASCADE,
    mergegroupid INTEGER NOT NULL REFERENCES public.merge_group(mergegroupid) ON DELETE CASCADE,
    created_by   VARCHAR(100),
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_merge_load_policy_group PRIMARY KEY (policyid, mergegroupid)
);
CREATE INDEX IF NOT EXISTS ix_mlpg_group ON public.merge_load_policy_group (mergegroupid);

-- Ambiguity guard 1: mapping a policy to a group whose OTHER mapped policies have an
-- overlapping section-count range is rejected (SQLSTATE 23P01 exclusion_violation).
-- The per-group advisory lock serializes concurrent mappings of the same group.
CREATE OR REPLACE FUNCTION public.merge_load_policy_group_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    new_min INTEGER;
    new_max INTEGER;
    clash   INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(4217, NEW.mergegroupid);
    SELECT min_sections, max_sections INTO new_min, new_max
      FROM public.merge_load_policy WHERE policyid = NEW.policyid;
    SELECT p.policyid INTO clash
      FROM public.merge_load_policy_group m
      JOIN public.merge_load_policy p ON p.policyid = m.policyid
     WHERE m.mergegroupid = NEW.mergegroupid
       AND m.policyid <> NEW.policyid
       AND p.min_sections <= new_max AND new_min <= p.max_sections
     LIMIT 1;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION 'HC17 policy % would overlap policy % on merge group % (ambiguous section-count range)',
            NEW.policyid, clash, NEW.mergegroupid USING ERRCODE = '23P01';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_merge_load_policy_group_guard ON public.merge_load_policy_group;
CREATE TRIGGER trg_merge_load_policy_group_guard
    BEFORE INSERT OR UPDATE ON public.merge_load_policy_group
    FOR EACH ROW EXECUTE FUNCTION public.merge_load_policy_group_guard();

-- Ambiguity guard 2: changing a mapped policy's range may not make it overlap another
-- policy mapped to any of the same groups.
CREATE OR REPLACE FUNCTION public.merge_load_policy_range_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    clash_group INTEGER;
BEGIN
    IF NEW.min_sections = OLD.min_sections AND NEW.max_sections = OLD.max_sections THEN
        RETURN NEW;
    END IF;
    SELECT mine.mergegroupid INTO clash_group
      FROM public.merge_load_policy_group mine
      JOIN public.merge_load_policy_group other
        ON other.mergegroupid = mine.mergegroupid AND other.policyid <> mine.policyid
      JOIN public.merge_load_policy p ON p.policyid = other.policyid
     WHERE mine.policyid = NEW.policyid
       AND p.min_sections <= NEW.max_sections AND NEW.min_sections <= p.max_sections
     LIMIT 1;
    IF clash_group IS NOT NULL THEN
        RAISE EXCEPTION 'HC17 policy % range %-% would overlap another policy on merge group %',
            NEW.policyid, NEW.min_sections, NEW.max_sections, clash_group USING ERRCODE = '23P01';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_merge_load_policy_range_guard ON public.merge_load_policy;
CREATE TRIGGER trg_merge_load_policy_range_guard
    BEFORE UPDATE OF min_sections, max_sections ON public.merge_load_policy
    FOR EACH ROW EXECUTE FUNCTION public.merge_load_policy_range_guard();
