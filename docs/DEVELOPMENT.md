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
| `lazyChunkGzipKB`: the largest page chunk | 11.5 | 9.5 |
| `sentryGzipKB`: the Sentry chunk (lazy, off the critical path) | 68 | 61.7 |

Each limit is the measure plus about 10%, except the page budget (about 20%:
the largest page, the Library, is the one that grows with features, and 10%
is a single new component). When it fails, first look for what
grew: a new dependency, a page imported eagerly instead of with `lazy()`, a
library pulled into the entry chunk. Raise a budget only on purpose, in the
same pull request as the change that needs it, with the new measure and the
reason in the description (a library that earns its weight, an SDK upgrade);
set it to the new measure plus about 10% and update the table above and the
comment in `size-budget.json`. Never raise it to get a red build green without
that explanation.

The script also exits 1, with its own message, when `dist/` is missing ("run
`npm run build` first"), and when there is no lazy `sentry-<hash>.js` chunk
(the SDK was bundled into another file, or `index.html` loads it up front):
a budget on a file that is not there would pass with 0 KB. It reads every
`<script type="module">` and `modulepreload` tag of `index.html`, not only the
first.

### Browser monitoring: scrubbing

`npm run check:scrub` runs the browser-monitoring scrubbers
(`src/lib/scrub.ts`) on sample client data (URLs, file names, organization and
brand names, e-mail addresses, the span shapes the SDK streams, error
chains) and fails if any of it survives. Run it after touching that file or the
Sentry setup (`src/lib/sentry.ts`). It compiles the file in memory with the
TypeScript already installed, so no test framework is needed. When you add a
rule to `scrub.ts`, add the sample that needs it, and check the sample is
really what fails: switch the rule off and the script must go red.

**Upgrading `@sentry/react`.** `package.json` pins it to an exact version (no
`^`) on purpose: a new SDK version can add span or event attributes the
scrubbers do not know, and a string attribute nobody scrubs goes to Sentry as is.
Bump it by hand (`npm install --save-exact @sentry/react@<version>`), then, in
the same pull request:

1. `npm run build && npm run size && npm run check:scrub`.
2. Re-run the browser proof (a manual step: it needs a browser, which CI does
   not install, and the repository has no browser-test dependency). Build a
   copy (`cp -r frontend /tmp/fe && cd /tmp/fe && npm ci && npm run build`),
   serve it (`npx vite preview`) and drive it with Playwright from outside the
   repository: mock `/api/auth/config` with a `sentry` block (DSN
   `https://abc123@o1.ingest.de.sentry.io/42`, `tracesSampleRate: 1`),
   `/api/me`, `/api/orgs` and a brand's `/overview` and `/library`, and
   intercept `https://*.ingest.de.sentry.io/**` (register it so that it wins
   over the `**/api/**` mock: the ingest path ends in `/api/42/envelope/`),
   recording every envelope body (gunzip it if needed).
3. In that run: open a page with client-looking data (a reference called
   `bouteille-secrete-2026.jpg`, an organization and a brand with names and
   slugs, a signed thumbnail link with `?token=SECRETTOKEN`), inject a large
   image loaded from a signed link, force a layout shift, click something
   slow, navigate between pages, throw errors whose message names the
   organization, the brand and a client site, and an `ApiError` as the `cause`
   of another error; hide the page so the Web Vitals are sent.
4. Grep all recorded bytes for the names, ids, the e-mail address, `.jpg`,
   `SECRETTOKEN` and the client site's host: zero matches. Also list the
   string attributes of every span (`attributes` of each item of the `span`
   envelope items) and look for any new key that carries a name, a path or a
   URL: if there is one, give it a rule in `scrub.ts` and a sample in
   `check-scrub.mjs`.
5. As a control, replace the functions of `scrub.ts` by identity functions in
   the copy: the same grep must now find matches. A proof that cannot fail
   proves nothing.

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
