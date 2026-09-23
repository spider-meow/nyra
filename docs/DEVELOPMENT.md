# Development guide

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[worker,dev]"
playwright install chromium
cd frontend && npm install
```

Run the product locally against a Supabase project (a separate one from
production):

```bash
nyra serve          # http://127.0.0.1:8000
nyra worker
cd frontend && npm run dev   # http://127.0.0.1:5173, proxies /api to :8000
```

After renaming or moving the package, reinstall (`pip install -e .`):
an editable install points at the old path otherwise.

## Tests

```bash
pytest
```

- `test_units.py`, `test_match.py`, `test_crawl.py`, `test_integration.py`
  need nothing: no network, no browser, no model download (the one CLIP
  test downloads the model if `open_clip` is installed).
- `test_cloud_*.py` need a disposable Postgres with pgvector:

  ```bash
  docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16
  TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres pytest
  ```

  `conftest.py` stubs the bits of Supabase's `auth`/`storage` schemas the
  migrations reference, applies any migration not yet applied, and gives
  tests an in-memory fake Storage. Never point `TEST_DATABASE_URL` at a
  real project.

CI (`.github/workflows/ci.yml`) runs `ruff check backend`, the full test
suite against a pgvector service container, and the frontend build.

Frontend checks: `npm run build` (runs `tsc --noEmit` first).

## Conventions

- **One pipeline, several stores.** Crawl and match logic lives once, in
  `crawl.py` and `match.py`; persistence goes through `CrawlStore` /
  `MatchStore`. A new backend is a new store, not a new crawler.
- **Pure where it can be.** Parsing, classification, grouping and the
  report take plain data and return plain data; I/O stays in the thin
  layer around them. That's what keeps most tests hermetic.
- **Thresholds live in `config.yaml`.** Anything an organization may tune
  goes in `ORG_OVERRIDABLE` with validation in `validate_overrides`.
- **Every write is an upsert on a natural key** (`filename`, `url`), so
  re-running any step is safe.
- **Long work is a job.** A web request never crawls, embeds or renders a
  report; it enqueues.
- **Outbound requests go through `netguard`.** Never create a bare
  `httpx.Client` for a URL a user supplied.
- **One query per screen.** Batch-sign Storage URLs
  (`storage.signed_urls`), aggregate in SQL instead of looping.
- **Per-item failures don't abort a loop.** A bad page or image is
  recorded and skipped.
- **Interface copy is French, formal (vous), and free of internal
  vocabulary** (no `phash`, `a_verifier` on screen; technical details go
  behind a disclosure).

## Extending

- **A new reference source** (a DAM export): implement `refs.RefSource`.
- **A new image-loading pattern**: `extract_images_from_html` for DOM
  attributes, `BACKGROUND_IMAGE_JS` for computed styles; add a test with a
  minimal HTML snippet.
- **A stubborn age gate**: add its selector in Settings > "Clics
  supplémentaires", or extend `DISMISS_OVERLAYS_JS`.
- **A new match signal**: a pure `classify_levelN`, wired into `compare`
  after the cheaper levels, plus the feature in both stores.
