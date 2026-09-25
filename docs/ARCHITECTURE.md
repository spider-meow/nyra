# Architecture

Nyra is a deterministic pipeline, not an agent: every step reads inputs,
does one job, and writes outputs. There is no LLM anywhere in the runtime
path — matching decisions come from perceptual hashes and CLIP embeddings,
reproducible given the same inputs and thresholds.

## Processes

```
 browser ──HTTPS──▶  web  (nyra serve)            worker  (nyra worker) × N
                     FastAPI + built interface     Chromium, CLIP
                     validates, stores, enqueues   claims jobs, runs them
                          │        │                    │        │
                          ▼        ▼                    ▼        ▼
                     ┌──────────────────────────────────────────────┐
                     │ Supabase: Postgres (+ jobs table), Storage,  │
                     │ Auth (JWTs verified locally by the backend)  │
                     └──────────────────────────────────────────────┘
```

- **web** answers every HTTP request quickly. Uploading a reference hashes
  it and stores it (seconds); anything slower — reading a site,
  comparing, computing embeddings, building a report — becomes a row in
  `jobs`. The web image has neither Chromium nor torch.
- **worker** claims queued jobs (`FOR UPDATE SKIP LOCKED`), at most one
  running job per brand, several brands (of one organization or not) in
  parallel across workers. It heartbeats every 30 s and while it reports progress; a job
  whose heartbeat stops (crash, redeploy) is marked failed by the next
  worker instead of staying "running" forever. Cancellation is a flag the
  worker checks between steps.
- The interface polls one cheap endpoint (`jobs/current`) — every 2 s
  while something runs, every 20 s otherwise — and refreshes the lists a
  finished job changed.

Job kinds: `crawl` (the chosen addresses of a brand, then compare),
`match`, `index` (fill in embeddings, mirror hashes and thumbnails —
queued after every upload, also backfills older rows), `report`.

## Organizations and brands

An organization is a client; it holds brands. A brand owns a library and
one or more sites (typically one per market: louis-xiii.com/us, /fr...),
and its references are only ever compared with images read on its own
sites. Exclusions, reports and the job queue are per brand; settings and
memberships per organization. Product routes live under
`/api/orgs/{org_id}/brands/{brand_id}/...`, the interface under
`/o/{org}/m/{brand}/...`.

## Pipeline

```
 reference upload ──▶ hashes + mirror hashes + thumbnail ──▶ index job: CLIP embedding
                                                                   │
 site URL ──▶ crawl: sitemaps (robots.txt Sitemap:, gzip) + internal links
              Chromium pages in parallel, cookie banners / age gates dismissed,
              images blocked in the browser (the DOM already lists them)
              ──▶ images downloaded through netguard, size-capped
              ──▶ same bytes seen before? reuse · too small? skip and remember
              ──▶ hashes + thumbnail + CLIP (batched) ──▶ stored
                                                                   │
                                                          match (incremental)
                                                                   │
                                  review in the interface ──▶ report (without false positives)
```

## One pipeline, two stores

`crawl.crawl_site` and `match.run_matching` don't know where data lives.
They talk to a store:

| Interface | CLI (SQLite + local disk) | Hosted (Postgres + Supabase Storage) |
|---|---|---|
| `crawl.CrawlStore` | `db.LocalStore` | `cloud.store.CloudCrawlStore` |
| `match.MatchStore` | `db.LocalStore` | `cloud.store.CloudMatchStore` |

Grouping matches by reference, collapsing CDN variants, the dashboard
numbers and the report itself are pure functions over plain dicts in
`report.py`, used by the API, the worker and the CLI alike.

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | `config.yaml` into typed dataclasses; per-organization overrides (whitelisted, validated, capped). |
| `netguard.py` | Refuses any outbound request to a non-public address (private ranges, loopback, link-local/metadata, CGNAT), per redirect hop, in httpx and in the browser. Size-capped reads. |
| `fetch.py` | Download (through netguard), decompression-bomb-safe decoding, size filter, hashes, thumbnail. |
| `crawl.py` | URL canonicalization, HTML/sitemap parsing (pure), overlay dismissal, the async crawler. |
| `match.py` | Hashes, CLIP (batched), pure classification, the vectorized comparison kernel, incremental orchestration, calibration sweeps. |
| `refs.py` | `RefSource` (CSV + folder today, a DAM export later), strict expiry-date parsing, reference features. |
| `report.py` | Grouping, dashboard numbers, report HTML/CSV. |
| `db.py` | SQLite schema and `LocalStore` for the CLI. |
| `cloud/api.py` | The web process. |
| `cloud/worker.py` | The job runner. |
| `cloud/jobs.py` | The queue (enqueue, claim, heartbeat, cancel, reap). |
| `cloud/store.py` | Postgres + Storage behind the store interfaces. |
| `cloud/db.py` | Pooled Postgres access, one query per screen (no N+1). |
| `cloud/storage.py` | Supabase Storage; signs URLs in batches. |
| `cloud/auth.py` | JWT verification (HS256 or JWKS) and membership/role checks. |

## Security model

- **No public sign-up.** Organizations are provisioned with the CLI;
  people join by invitation. Disable "Allow new users to sign up" in the
  Supabase dashboard too, or anyone holding the public anon key can still
  create an (organization-less, powerless) account.
- **Every route** under `/api/orgs/{org_id}` verifies the JWT locally and
  the caller's membership; brand routes then check the brand belongs to
  that organization. Admin-only routes (brands, sites, library, crawls,
  settings, reports) check the role. Reviews are open to every member and
  are checked against the brand's own matches.
- **The backend uses the service role**, which bypasses RLS; every query
  filters on `org_id` itself. RLS policies are the safety net for any
  other access path.
- **Crawling is SSRF-guarded** (`netguard`), page counts are capped
  (`crawl.max_pages_limit`), downloads and uploads are size- and
  pixel-capped.
- **Headers:** CSP (no inline scripts, only the Supabase origin for
  connect/img), `X-Frame-Options: DENY`, `nosniff`, strict referrer.

## Why it's built this way

**Two-level matching.** Perceptual hashes are microseconds per pair and
catch the common case (same image re-encoded, resized, flipped). CLIP
catches crops and overlays but is costlier and softer, so it only
decides pairs the hashes didn't. See `MATCHING.md`.

**Jobs in Postgres, not in memory.** A restart loses nothing, several web
instances can sit behind a load balancer, the worker scales on its own,
and history (`jobs`, `crawl_runs`) is queryable.

**Thumbnails stored next to originals.** Lists and reports never load a
full-size image; the browser gets short-lived signed URLs, signed in one
request per list.

**Incremental matching.** A finished pass is remembered with a signature
of the thresholds and model; the next pass only compares what's new,
unless the signature changed.
