"""Multi-tenant, Supabase-backed persistence/storage/auth layer.

Everything in this package is additive: the local, single-tenant product
(`rightswatch.db`, `rightswatch.api`, SQLite + local disk, no accounts)
is untouched and keeps working exactly as before. This package targets
the schema in `supabase/migrations/` instead — Postgres with `org_id` on
every table, Supabase Storage instead of local disk, Supabase Auth JWTs
instead of no accounts at all.

`rightswatch.cli`'s `ui` command switches between the two automatically
based on whether `DATABASE_URL` is set — see cli.py's `ui_cmd`.
"""
