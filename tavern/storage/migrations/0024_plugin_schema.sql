-- Stage M1 (ADR 0019): plugins may own tables. A plugin's folder can hold `migrations/NNNN_name.sql`; the
-- number of the last one applied is kept here. Every table, index and trigger such a file creates has to
-- start with `p_<id>_` (plugins/schema.py checks it).
ALTER TABLE plugins ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 0;
