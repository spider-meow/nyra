-- Extensions RightsWatch relies on.
--
-- pgcrypto: gen_random_uuid() for primary keys (Postgres has a core
--   gen_random_uuid() since v13, but Supabase images ship pgcrypto anyway
--   and some hosted Postgres versions still need it — enabling it is free
--   insurance).
-- vector (pgvector): storage type for CLIP embeddings (see
--   0003_pipeline_core.sql). Both are on Supabase's allow-listed extension
--   list and enable cleanly from the SQL editor or the CLI.

create extension if not exists pgcrypto;
create extension if not exists vector;
