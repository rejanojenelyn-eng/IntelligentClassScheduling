-- 2026-09-30  HC6: add the confirmed institutional 3:00-6:00 PM block.
--
-- Configuration-only data change (no schema change). HC6 now validates whole
-- start-end blocks: scheduler.STANDARD_BLOCKS plus the admin-configured
-- scheduler_config.hc_time_slots. 15:00-18:00 is used by Published schedules
-- (e.g. BEED1, BSA3) and historical data, and was confirmed as an allowed
-- institutional block, so it is configured here exactly as Settings -> Time Slots
-- would store it. Other slots are left untouched. Idempotent.
--
-- Installs with no stored hc_time_slots row use database._SCHEDULER_CONFIG_DEFAULTS,
-- which includes the same block.

UPDATE scheduler_config
   SET config_value = (config_value::jsonb || '[[15,0,18,0]]'::jsonb)::text
 WHERE config_key = 'hc_time_slots'
   AND NOT (config_value::jsonb @> '[[15,0,18,0]]'::jsonb);
