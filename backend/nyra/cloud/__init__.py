"""The hosted, multi-organization product: Postgres + Supabase Storage + Supabase Auth.

- `api`     the web process (`nyra serve`): HTTP API and the built interface
- `worker`  the job runner (`nyra worker`): crawl, match, index, report
- `jobs`    the durable job queue both of them share
- `store`   Postgres/Storage behind the pipeline's store interfaces
- `db`, `storage`, `auth`  persistence, files, session checks

The crawler, the matcher and the report are the same code the CLI runs on
SQLite (`nyra.crawl`, `nyra.match`, `nyra.report`); only persistence
differs.
"""
