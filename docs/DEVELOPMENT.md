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
suite against a pgvector service container, and the frontend build, bundle
budget and scrub check.

Frontend checks: `npm run build` (runs `tsc --noEmit` first), then
`npm run size` and `npm run check:scrub` (below).

### Bundle-size budget

`npm run size` (after `npm run build`; not part of `build`, so local builds
stay fast) measures the JavaScript the browser needs for the first screen and
fails when it grows past `frontend/size-budget.json`. First load is what
`dist/index.html` loads up front: its `<script type="module">` and its
`modulepreload` links. Every other file in `dist/assets` is lazy (a page
opened later, the Sentry SDK). It prints one line per chunk (raw KB, gzip KB,
share of its budget) and exits 1 naming the chunk that went over.

| Budget (gzip KB, 1 KB = 1000 bytes) | Limit | Measured on 1 Oct 2026 |
|---|---|---|
| `firstLoadGzipKB`: every chunk of `index.html` | 210 | 191.4 |
| `entryGzipKB`: the entry chunk alone | 24 | 21.9 |
| `lazyChunkGzipKB`: the largest page chunk | 10.5 | 9.5 |
| `sentryGzipKB`: the Sentry chunk (lazy, off the critical path) | 67 | 61.0 |

Each limit is the measure plus about 10%. When it fails, first look for what
grew: a new dependency, a page imported eagerly instead of with `lazy()`, a
library pulled into the entry chunk. Raise a budget only on purpose, in the
same pull request as the change that needs it, with the new measure and the
reason in the description (a library that earns its weight, an SDK upgrade);
set it to the new measure plus about 10% and update the table above and the
comment in `size-budget.json`. Never raise it to get a red build green without
that explanation.

`npm run check:scrub` runs the browser-monitoring scrubbers
(`src/lib/scrub.ts`) on sample client data and fails if any of it survives;
run it after touching that file or the Sentry setup (`src/lib/sentry.ts`).
It compiles the file in memory with the TypeScript already installed, so no
test framework is needed.

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
