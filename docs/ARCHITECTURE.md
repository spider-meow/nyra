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
queued after every upload, also backfills older rows; an administrator can start it from Réglages > Général > Maintenance, `POST .../jobs/index`), `report`, `locate` (checks one
reference against every stored site image to find its copies and crops; see `MATCHING.md`), `check` (reads again only the pages a person gives, 50 at most, `POST .../jobs/check`: no sitemap, no link followed, no comparison, and no crawl run, since a run would stand for a read of the whole site). A page that answered with another page (a redirection), failed, was blocked by robots.txt or whose read did not end is never counted as gone, and the job says how many were left out. A check counts once a full read of the site has finished; deploy the worker before the web process, so a page created after the migration always records when its read started.

## Organizations and brands

An organization is a client; it holds brands. A brand owns a library and
one or more sites (typically one per market: louis-xiii.com/us, /fr...),
and its references are only ever compared with images read on its own
sites. Exclusions, reports and the job queue are per brand; settings and
memberships per organization. Product routes live under
`/api/orgs/{org_id}/brands/{brand_id}/...`, the interface under
`/o/{org}/b/{brand}/...` (English since October 2026; the old French addresses
redirect, see `frontend/src/lib/paths.ts`).

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
| `variants.py`, `cloud/variants.py` | Groups the crops and resizes of one site photo (CLIP candidates, keypoint check, union into `variant_group`); the worker runs it after each comparison. |
| `cloud/locate.py` | The `locate` job's database side: reads the brand's stored images, runs `verify.locate`, replaces the reference's `reference_locations`. |
| `labels.py`, `cloud/labels.py` | Labels learned from examples a person chose: the score of an image for a label is the mean similarity of its 3 closest examples (CLIP). Types (one per image) take the best score; content labels (several) pass a threshold. Proposals are computed from the embeddings when asked, nothing is stored but the decisions. |
| `logos.py`, `cloud/logos.py` | Is a whole image a logo? CLIP zero-shot on the stored embeddings against precomputed descriptions (`resources/logo_text.npz`); the cloud side scores the site images, says which are set aside (setting on, score above the threshold, not put back) and puts one back. The comparison, "Droits non vérifiés" and the search for a reference's copies leave those images out; see `MATCHING.md`. |
| `refs.py` | `RefSource` (CSV + folder today, a DAM export later), strict expiry-date parsing, reference features. |
| `report.py` | Grouping, dashboard numbers, report HTML/CSV. |
| `db.py` | SQLite schema and `LocalStore` for the CLI. |
| `cloud/api.py` | The web process: `CloudSettings` and `create_app`, which wires the modules below in a fixed order (middleware, `/assets`, API routes, then the catch-all page). |
| `cloud/routes/common.py` | What the route modules share: the request context (`Ctx`), the auth and brand dependencies, small helpers. |
| `cloud/routes/` (`session`, `brands`, `members`, `library`, `collections`, `labels`, `occurrences`, `jobs`, `matches`, `site_images`, `logos`, `reports_settings`) | The API routes, one module per domain, each exposing a `router`. |
| `cloud/routes/interface.py` | The built interface (single-page app), gzip and security headers, error handlers. |
| `cloud/presence.py` | Where a visual is still online, page by page: `still` (seen by the last finished read of the site), `gone` (the page was read again with success and the image was not on it), `unknown` (the page was not read again, or not since `image_pages.last_seen` and `pages.links_tracked` exist). Counts per site version and section, and the pages; `GET .../visuals/{reference_id}/presence`. Images and links are never deleted when they disappear from a site, so nothing is stored: the state comes from the dates. While the last finished read of a site is **suspect** (`crawl_runs.suspect`, set when the read ends, see `MATCHING.md`), no page of it is `gone`. `replaced_references` lists the visuals replaced, which the home screen and the report leave out of "expired and online". |
| `cloud/worker.py` | The job runner. |
| `cloud/jobs.py` | The queue (enqueue, claim, heartbeat, cancel, reap). |
| `cloud/store.py` | Postgres + Storage behind the store interfaces. |
| `cloud/db.py` | Pooled Postgres access, one query per screen (no N+1). |
| `cloud/storage.py` | Supabase Storage; signs URLs in batches and remembers a bounded number of them, so an image keeps the same URL while it is valid. |
| `cloud/auth.py` | JWT verification (HS256 or JWKS) and membership/role checks. |
| `cloud/usage.py` | Quotas and usage per organization: the counters (`usage_counters`, added to by the worker), the usage against the limits of `config.QuotaConfig` (the Nyra team overrides them per organization), the refusal sentences (`check`) and the day's e-mail allowance (`claim_email`). |
| `cloud/insights.py` | Statistics for the Statistiques page and the back office, from the `insights` views. |
| `observability.py` | JSON logs tagged with the job in progress, optional Sentry. |

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
- **A test looks for the holes** (`backend/tests/test_isolation.py`): two organizations, each with
  data of every kind, and every route of the application called as an admin and as a client
  with (1) the organization of B, (2) the brand of B under the caller's organization, (3) the
  caller's brand with identifiers of B in the path, the body or the query, (4) the caller's own
  valid identifiers (the handler really runs and answers 200, so a 404 or a 422 cannot pass for
  isolation) and (5) mixed calls (a resource of the caller and one of B). No answer may be a 5xx or
  carry data of B (body and headers); no row of B may change (tables with or without `org_id`); no row of the caller
  may point to B; a call made only of B's identifiers leaves the caller's data alone. **Adding a
  route**: the test finds it by itself; give it a case in `BODIES` (a valid body or query for a
  tenant) if it takes a body or an identifier in its query, or list it in `PUBLIC` / `USER_LEVEL`
  with the reason it needs no organization check. A route left out fails the test. Limits: it
  catches a filter that is missing where B's data or identifiers can reach, not every possible
  flaw (a query nobody calls with B's data, a leak that needs a particular row); the `/api/staff/*`
  routes are only checked to refuse members (their nominal behaviour is in `test_cloud_api.py`);
  Storage's RLS policies are never exercised, the backend signs with the service role; the
  Supabase side of invitations is stubbed.
- **Storage paths** start with the organization (`{org_id}/...`, references
  `{org_id}/{brand_id}/...`); a file name is folded to one segment (`safe_key`) and
  only looked up among the brand's own references, so a name or a path of another
  organization gives nothing.
- **Members are reviewed by hand** (the test cannot see a stale or wrong
  account): Back office > Comptes lists the administrators of every
  organization, since when and their last sign-in (see DEPLOYMENT.md, "Reviewing
  the members").
- **Crawling is SSRF-guarded** (`netguard`), page counts are capped
  (`crawl.max_pages_limit`), downloads and uploads are size- and
  pixel-capped.
- **Quotas per organization** (`quota` in `config.yaml`, `cloud/usage.py`): pages read per month (a read stops at
  what is left, a new one is refused), brands, addresses, library, storage, members, invitations per day. Set by the
  Nyra team only; the client sees its usage in Réglages > Usage.
- **Headers:** CSP (no inline scripts, only the Supabase origin for
  connect/img, plus the Sentry ingest origin in connect when
  `SENTRY_BROWSER_DSN` is set and valid), `X-Frame-Options: DENY`, `nosniff`,
  strict referrer.
- **Static files:** `/assets/*` (content-hashed by Vite) is served
  `Cache-Control: public, max-age=31536000, immutable`; `index.html` is
  `no-cache`. Responses over 1 KB are gzipped.

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
request per list. The library list returns metadata only and signs nothing;
the interface asks `POST .../library/thumbs` for the thumbnails of the cards on
screen (200 names at most per call, 100 sent by the interface: thumbnail, else working image, else
original), and `GET .../library/{filename}/url` for an original when the edit
modal opens. Signed URLs are remembered per organization (bounded) and handed
out again only while they have at least 40 minutes left, so a browser keeps an
image in its cache and never holds an expired link. The same response carries the collections; the folder tree, its counts and the search (every word must match a name, tag, collection path or credit; a search ignores the folder being viewed) are computed in the browser. Ceiling: metadata for about
10,000 references in one response; beyond that, filter and paginate on the
server.

**One page for every image read ("Images en ligne").** Tabs: `qualify` (matches nothing in the library, not ignored, and not a logo when the brand sets them aside), `found` (a visual of the library is in it), `logos` (every recognised logo), `ignored` (exclusions) and `all`. `GET .../site-images?view=` returns one tab, `limit` entries from the start, narrowed by `site` and `q`, with the totals of every tab; only the thumbnails of the entries returned are signed. The entries are grouped one per photo (same bytes under several addresses, crops of one photo), in `routes/common.py` (`online_images`).

**"À traiter" is the triage of matches.** `report._collapse_hits` merges, for one reference, the files of one photo: same bytes under several addresses, and the crops and resizes already grouped (`variant_group`). A hit carries `photo_key`, `file_count` and `versions` (the site versions it is on); its decision is the common one, or none while the files are not all decided alike. The interface (`pages/review/rows.ts`) puts one row per `photo_key` under the first reference that matches it, with how sure the match is, and two decisions: « C'est bien lui » (`retenu`: the visual is then to be replaced, see « Visuels à remplacer ») and « Faux positif » (`ecarte`). `traite` is the manual step « J'ai remplacé », taken on the page of a visual. **Checking removals.** A photo marked « Remplacé » (`traite`) carries `removal`, shown on the page of a visual, from the last finished read of each site (`report._removal`, columns of `db.match_rows`): `online` (a file was seen again since the mark: `site_images.last_seen` at or after the read's start), `pending` (a site was not read since the mark), `unread` (a page that showed it was not read again: `pages.crawled_at` before the read's start), `gone` (every page read again, nothing seen). Nothing is stored: the images and pages are never deleted when they disappear from a site, so the answer is worked out from the dates. « Relire les sites » is a full read (`POST .../jobs/crawl`, `fresh`, as many pages as allowed): a read that resumed leaves the old pages `unread`, never `gone`.

**A suspect read.** When a full read ends, the worker stores its health on the run (`crawl.read_health`, `suspect_reason`: no page read, too many errors, or fewer than 70 % of the known images seen again) and `GET .../visuals` and `.../presence` say which sites are suspect; the interface shows « La dernière lecture de ce site semble incomplète : rien n'a été marqué retiré ». `report._removal` reads `suspect` in that case, never `gone`. A consent wall that stays after the overlays are closed is recorded on the page (`CONSENT_WALL`), never `gone`.

**The page of a visual** (`/visuals/<reference id>`, `pages/VisualPage.tsx`, `pages/visual/`) shows, from `GET .../visuals/{reference_id}/presence` (`cloud/presence.py`): how many pages still show the visual at the last read, per site version and section, the pages (`still`, `gone`, `unknown`) 50 at a time, and its photos. It opens from « Visuels à remplacer » and from the panel of « À traiter » (« Voir où il est en ligne »); the way back goes where the person came from. The figures follow the last finished read of each site: a read that resumed leaves pages `unknown`, never `gone`.

**« Visuels à remplacer »** (`/visuals`, `pages/Visuals.tsx`, `GET .../visuals`): the visuals a person confirmed (a photo decided « à retirer » or « retiré »; unlimited rights never), soonest expiry first, each with its pages `still` / `gone` / `unknown` (`cloud/presence.py`, every photo not set aside counts). A visual is **replaced** when it has pages, all were read again, and none shows it; a page that answers 404 or 410 counts as gone, a page not read again never does. The manual step is a mark on each page of the visual (`page_replacements`, one row per visual and page, `POST .../visuals/{reference_id}/replaced`, any member): it tells the person where they are, shown beside what the last read says only once the page was read again after the mark (`checked`: `pages.read_started_at` after `page_replacements.marked_at`): « Remplacée · confirmée » or « Marquée remplacée · encore en ligne » then, and « Remplacée · à vérifier » until then, and counted in the list (« N marquées remplacées »). It never makes a visual replaced: only the reads do, so the list does not change when pages are marked. The photo-level decision `traite` is no longer set by the interface.

**Bounded jobs.** The index job reads its candidates in chunks (keyset on `id`,
so an image that can never be read cannot loop the job) and writes each batch
in one transaction; the back office totals are SQL aggregates and its brand
table is capped.

**Incremental matching.** A finished pass is remembered with a signature
of the thresholds and model; the next pass only compares what's new,
unless the signature changed.
