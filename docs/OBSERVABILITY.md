# Observability

Three layers, each answering a different question:

| Question | Where | Setup |
|---|---|---|
| How does a brand's site and pipeline behave? (image count, average weight, images per second, crawl duration, false positives…) | **Statistiques** page of each brand, `/o/<org>/m/<brand>/statistiques`, organization admins | nothing: built in |
| How is the whole platform doing? (every brand of every organization side by side, job queue, failures, throughput) | **Back office**, `/interne`, Nyra team only | `nyra cloud-staff --email …` |
| Is it up? What broke, and when? Are pages slow for real users? Trends over months, alerts | **Grafana** (metrics, logs, SQL) + **Sentry** (exceptions on the server and in the browser, page speed) | below |

## What is measured

Every crawl records, in `crawl_runs.metrics` (see `CrawlStats` in
`backend/nyra/crawl.py`):

- **Volumes:** sitemap URLs, pages read and failed, HTTP status of each
  page, images found / already known (not downloaded again) / downloaded /
  same bytes as another URL / rejected (too small, unreadable), new images
  with their bytes, pixels, thumbnail bytes and format.
- **Time per phase:** discovery (robots + sitemaps), page rendering in
  Chromium, image downloads, decode + hashes + thumbnails, CLIP
  embeddings, uploads + database writes, CLIP model loading, total
  duration. Pages render in parallel, so the phase sums can exceed the
  duration: they say where the effort goes, not the wall-clock split.

Site images keep their weight on the site (`byte_size`), their original
format (`format`: Nyra only stores a JPEG working copy) and what Nyra
stores for them (`stored_bytes`); references keep the weight of the
uploaded original. From the `insights` migration on: older rows have none
and are left out of averages.

Jobs keep their timings (`created_at`, `started_at`, `finished_at`), and
their `result` now carries `metrics` (index: images embedded, embedding and
loading time) and `match_metrics` (comparison: full or incremental,
references × site images, pairs compared, hits by level, seconds).

Derived in the `insights` views: pages per minute, images scanned per
second, CLIP images per second, seconds of rendering per page, average
weight of a new image, images per page, queue wait, run time, false
positive rate per confidence band and per level.

## Back office access

```bash
nyra cloud-staff --email arthur@example.com           # grant (invites if no account)
nyra cloud-staff --email arthur@example.com --remove  # revoke
```

Staff see `/interne` (and a "Back office Nyra" link in the sidebar). The
list lives in `platform_staff`, readable only by the backend. Staff are not
members of every organization: "Détail" on a brand opens its Statistiques
page only if the account is also admin there (`nyra cloud-invite --role
admin`).

## Sentry (exceptions and slow requests)

1. Create a Python project in Sentry (EU region if the clients require
   it) and copy its DSN.
2. Set `SENTRY_DSN` for both processes (web and worker). Optional:
   `NYRA_ENV` (production, staging…), `SENTRY_TRACES_SAMPLE_RATE` (share of
   requests and jobs timed, default 0.1), `NYRA_RELEASE`.
3. The Docker images ship the SDK (the `observability` extra). Locally:
   `pip install -e ".[observability]"`.

Worker errors are tagged with `org_id`, `job_kind` and `job_id`. Request
bodies and personal data are not sent (reference filenames and client URLs
are client data).

### Sentry in the browser (errors and slow pages for real users)

Answers "are pages slow or broken for the people using them?" in a way
the server cannot: it sees what the browser sees (network, rendering).

1. Create a **JavaScript / React** project in Sentry (EU region if the
   clients require it; it is a different project from the Python one) and
   copy its DSN.
2. Set `SENTRY_BROWSER_DSN` on the `web` process (empty = off). The server
   hands it to the page in `GET /api/auth/config`, with `NYRA_ENV`,
   `NYRA_RELEASE` and `SENTRY_TRACES_SAMPLE_RATE` (default 0.1: one page
   load or navigation in ten is timed; errors are always sent), and opens
   the Content-Security-Policy to that DSN's ingest host and nothing else.
   A DSN that is not `https://<key>@<host>/<project>` is ignored: nothing is
   loaded, nothing is allowed. No key lives in the build: a change of DSN
   is a restart, not a rebuild.
3. The SDK is its own file (`assets/sentry-*.js`, ~61 KB gzip), downloaded
   after the page has loaded and only when a DSN is set. With no DSN the
   browser never fetches it.

**What is collected:** unhandled errors and errors caught by the page error
screen; one *transaction* per page load and per navigation, named by route
pattern (`/o/:slug/m/:brand/bibliotheque`), with the API calls it made
(`GET /api/orgs/:id/brands/:id/overview`), the files it loaded, and the Web
Vitals: LCP, CLS and TTFB (FCP too) on the page-load transaction, INP on
its own span for the slowest interaction. API calls of the same site carry
trace headers, so the server's own Sentry traces join the browser's.
No session replay, no profiling, no user, no cookies, no headers, no
request or response bodies.

**What is scrubbed** (`frontend/src/lib/scrub.ts`, applied in `beforeSend`,
`beforeSendTransaction`, `beforeSendSpan` and `beforeBreadcrumb`): reference
file names, site URLs, organization and brand names and slugs, and e-mail
addresses are client data and never leave the browser.

- page URLs become route patterns, no query string, no fragment (invitation
  links carry a token in it);
- API URLs lose ids and file names (`/library/<name>/url` becomes
  `/library/:file/url`), query strings keep the parameter names only;
- links to other hosts (signed Storage links, fonts) shrink to their origin;
- the message of an error from the API (`ApiError`, written for the user,
  it can name a file) is replaced by `HTTP <status>`; any other error text
  that still names an image or document is dropped, e-mail addresses and
  ids are masked;
- breadcrumbs are limited to navigations and API calls (no console, no
  clicks: their text can hold file names); element selectors lose their
  `alt`, `title` and `aria-label`.

To check the scrubbing after a change: `cd frontend && npm run check:scrub`
(sample data in, nothing private out; it runs in CI).

**Reading slow pages:** *Performance > Web Vitals* lists the routes with
their LCP, INP, CLS and TTFB scores; *Performance > Transactions* sorts the
same routes by p75 duration, and a transaction's waterfall shows which API
call or file made it slow (`GET /api/.../library` taking 2 s, a page file
arriving late). A route name is a page, not a client: to see whether one
client's data is slow, use the Statistiques page and the server traces.
Sampling means few events on a quiet day: look at weeks, not hours.

**Alerts worth having:** LCP p75 per route above 2.5 s (the "good"
threshold) for 30 minutes; INP p75 per route above 200 ms; the rate of
JavaScript errors per release (an alert on "new issue" in production, and on
errors per user session after a deploy).

**Known limits.** Nothing was run against a real Sentry project when this
was written: the checks used a fake ingest. Source maps are not uploaded,
so stack traces show minified names. Ad blockers can block the ingest host,
so numbers are a floor. The SDK (v11) sends classic transactions here
(`traceLifecycle: "static"`); its newer span streaming mode is not used.

## Logs

`NYRA_LOG_FORMAT=json` makes both processes write one JSON object per line,
with `org_id`, `job_id` and `job_kind` while a job runs:

```json
{"ts": "2026-09-29T16:09:12+00:00", "level": "info", "logger": "nyra.worker", "process": "worker", "message": "job 3f… finished", "org_id": "03…", "job_id": "3f…", "job_kind": "crawl"}
```

On the server, the Grafana agent (Alloy) reads the Docker logs and sends
them to Loki; query them by brand with
`{container="nyra-worker-1"} | json | org_id="…"`.

## Grafana

Grafana Cloud's free tier covers this volume (metrics, logs and alerting,
with a few users and limited retention: check the current limits). It
connects to three things:

### 1. Postgres: the business numbers

Create a read-only role that sees the `insights` views and nothing else
(no embeddings, no credentials, no reference files). In the Supabase SQL
editor:

```sql
create role grafana_reader with login password '<a long random password>';
grant usage on schema insights to grafana_reader;
grant select on all tables in schema insights to grafana_reader;
alter default privileges in schema insights grant select on tables to grafana_reader;
-- The views read the base tables as their owner; the role itself gets no grant on public.
```

Add a PostgreSQL data source in Grafana with the **session pooler** host
(`aws-0-<region>.pooler.supabase.com:5432`), user
`grafana_reader.<project-ref>`, TLS required.

Starter panels:

```sql
-- Crawl duration per brand (time series)
select started_at as time, brand_name as metric, duration_seconds as value
from insights.crawl_runs where $__timeFilter(started_at) and status = 'done' order by 1;

-- Throughput: pages/min, images scanned/s, CLIP images/s (one panel each: one scale per panel)
select started_at as time, brand_name as metric, clip_images_per_second as value
from insights.crawl_runs where $__timeFilter(started_at) and clip_images_per_second is not null order by 1;

-- Where the time goes (stacked bars, last 30 days)
select brand_name, sum(render_seconds) as render, sum(download_seconds) as download,
       sum(process_seconds) as process, sum(embed_seconds) as clip, sum(store_seconds) as store
from insights.crawl_runs where started_at > now() - interval '30 days' group by 1;

-- Storage per brand
-- Weight on the sites vs what Nyra stores (working copies + thumbnails), per brand
select org_name, brand_name, total_bytes, stored_bytes from insights.site_images order by stored_bytes desc nulls last;

-- Jobs: failure rate and p95 duration per kind, 7 days
select kind, count(*) filter (where status = 'error')::float / count(*) as failure_rate,
       percentile_cont(0.95) within group (order by run_seconds) as p95_seconds
from insights.jobs where created_at > now() - interval '7 days' group by kind;

-- False positives per confidence band
select confidence, sum(false_positives)::float / nullif(sum(reviewed), 0) as fp_rate
from insights.matching group by confidence;
```

Alerts worth having (Grafana alerting on these queries):

- a job failed in the last hour: `select count(*) from insights.jobs where status = 'error' and finished_at > now() - interval '1 hour'` > 0;
- the queue is stuck: oldest queued job older than 15 minutes;
- a crawl took more than twice its brand's usual duration;
- storage past 80 GB (the Supabase Pro plan includes 100 GB).

The queue itself (`jobs`) is not in the `insights` schema. For queue
alerts, add `create view insights.queue as select org_id, kind, status,
created_at, heartbeat_at from public.jobs;` or grant `select (org_id,
kind, status, created_at, heartbeat_at) on public.jobs` to the role.

### 2. The server: CPU, memory, disk

Install Grafana Alloy on the VPS (Grafana Cloud gives the one-line
command). It ships host metrics (CPU, RAM, disk, network) and the Docker
logs. That's where Chromium's memory and the worker's CPU during crawls
show up.

### 3. Uptime

A synthetic check (Grafana Cloud or Better Stack) on
`https://<site>/api/healthz`, which also checks the database.
