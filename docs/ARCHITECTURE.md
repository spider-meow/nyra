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
  running job per **organization** (its brands take turns), organizations
  in parallel across workers. It heartbeats every 30 s and while it reports progress; a job
  whose heartbeat stops (crash, redeploy) is marked failed by the next
  worker instead of staying "running" forever. Cancellation is a flag the
  worker checks between steps.
- The interface polls one cheap endpoint (`jobs/current`) — every 2 s
  while something runs, every 20 s otherwise — and refreshes the lists a
  finished job changed.

**One pass at a time per organization.** Queue rules (`cloud/jobs.py`): `enqueue` takes an advisory lock per
organization, `claim` skips an organization that has a job running, and the unique index `jobs_one_running_per_org`
backs both. A job started **by hand** (`manual`: `crawl`, `check`, `locate`, `match`, `report`) is refused with a 409 as
soon as another job but an `index` is queued or running anywhere in the organization; the answer names the job (kind,
brand, who started it, since when) and `jobs/current` carries the same in `organization` (the jobs of the other
brands) so the interface disables the buttons and links to that brand. Who: the e-mail for an administrator, « un
membre de l'équipe » for a `client` member (decided in `jobs.holder`, on the server, from the role of the person who
asks). What **follows an action** is queued behind without error and never blocks a launch: the indexing after an
upload or an adoption, the comparison after an exclusion or a change of the logos setting (`behind_running`); an `index`
asked by hand joins the one already waiting. Cancelling is never refused (per brand: from the brand of the running
job). A job that crashes or overruns leaves `running` (`reap_stale`, `fail_running`), which frees the organization. The
monthly pass (AXE-523) is a launch by the system: it must queue without being refused, to wait for its turn. The reverse holds too: a monthly read still **waiting** (`params.scheduled`) does not stop a launch by hand (`enqueue(manual=True)` ignores it, `claim` serves the manual job first and the scheduled one after); a scheduled read already **running** blocks like any other job. One running job per organization stays guaranteed by the unique index.

**Global caps (AXE-530).** On top of one job per organization, `claim` keeps two caps, all organizations together:
`NYRA_MAX_HEAVY_JOBS` (default 1) on the jobs that read sites (`crawl`, `check`, `locate`) and `NYRA_MAX_MODEL_JOBS`
(default 1) on those that analyse images (`index`, `match`: CLIP model); `report` has none. A job takes a place of its
own kind only, and a running job whose `heartbeat_at` is older than `STALE_AFTER_SECONDS` holds none (a dead worker's
row). The count and the take are serialized by a transaction-level advisory lock (`jobs:claim`), because the unique
index cannot see other organizations. The excess stays `queued`: nothing is refused. Among the jobs that may start,
the organization whose last job started least recently goes first, then the oldest job (a `locate` after the others), so
an organization with many queued jobs does not push the others back. `jobs/current` marks each queued job of the brand
`waiting_for_capacity` when its organization runs nothing while its kind has no place: a yes or no, with nothing about
the other organizations' jobs. A worker process runs one job at a time and one `worker` container is started, so the caps
only matter with two workers or more (see `DEPLOYMENT.md`).

**One domain at a time (AXE-530).** `claim` also keeps a `crawl` or `check` queued while a running job reads one of
its domains (host without `www.`, any organization): a `crawl` reads the sites of `params.site_ids`, or all the
brand's sites when none is given; a `check` reads the sites that are the keys of `params.pages` (`_hosts` in
`cloud/jobs.py`). The other kinds read no site. When the domain cannot be told (the sites were deleted, odd params),
the job conflicts with nothing and starts: the worker then fails it with its own message, rather than the job
waiting for ever. A job held back by a domain is `waiting_for_capacity` like one held back by the cap.

**Monthly read (AXE-523, `cloud/schedule.py`).** A brand whose schedule is on (`brand_schedules`, off by default) is read
once a month: a `crawl` with `fresh` and the comparison after it, queued by the worker, `created_by` NULL (« la lecture
mensuelle automatique » in `jobs.holder`), `params.scheduled`. *When:* the brand's day (1 to 28) and an hour, a quarter-hour
slot between 02:00 and 05:00 UTC; the default day (1 to 7) and the slot come from the brand's uuid, never `hash()`, so
customers spread out. *Due* (`is_due`, pure): on, this month's date and hour passed, not queued yet this month: a worker
stopped on the day queues it once when it is back; a month is never read twice, whatever the restarts or a change of day;
`enable` on a brand never read whose date has passed waits for next month; on a brand already read it changes nothing to `last_run_at` (a new day or a switch off and on skips no month and reads none twice). *Atomic:* `enqueue_due` claims a brand with
`UPDATE ... WHERE <the due test> RETURNING` and queues the `crawl` in the same transaction (one savepoint per brand), so
two workers queue one read and a failure leaves the brand due (logged, tried again 5 minutes later; the oldest read first, at most 100 brands looked at per pass, and one full trace per brand and hour). It goes through
`jobs.enqueue` without `manual`: it waits behind a job of the same organization (AXE-529), the global caps and the domain rule
(AXE-530) and is never refused; if a *scheduled* read of the brand already waits or runs, that is the month's read and nothing is
added; if the read in the way was started by hand (maybe partial), the brand is left as it was and tried again at the next pass. A brand without a site is marked done with a warning (else it would be retried for ever). *Per pass:* at most
`MAX_PER_TICK = 3` brands, because after a stop of the worker dozens can be due; the rest go at the next pass. *Order:*
`claim` serves a scheduled job after the jobs started by hand (one more key of its `ORDER BY`, before the fairness
between organizations). Each launch writes one `schedule.run` row in the audit log (identifiers and counters, no user).
*Switch:* `NYRA_SCHEDULER=0|false|off` stops it. *Limit:* the worker looks every 5 minutes but not during a job (it runs
one at a time), so a read can start a few hours late; it could not have started before the running job ended anyway.

*Setting (`cloud/routes/schedule_settings.py`):* `GET .../brands/{brand_id}/schedule` (any member) gives `{enabled, day_of_month, default_day, next_run_at, last_read_at}`; a brand without a row reads as off, with its default day. `PUT` (administrators) takes `{enabled, day_of_month}` (nothing else accepted; the day, 1 to 28, is required to switch on and ignored to switch off), calls `schedule.enable` / `disable` and writes `schedule.enable` (with the day) / `schedule.disable` in the audit log in the same transaction, then answers like the `GET`. A `PUT` that asks for what already is (same state, same day) changes nothing and so writes no trace; one that only changes the day writes one `schedule.enable` with the new day. The text shown to the person says « tôt le matin » (the slots, 02:00 to 04:45 UTC, are 03:00 to 05:45 in winter and 04:00 to 06:45 in summer in Paris). `next_run_at` comes from `schedule.next_run` (pure, same rule as `is_due`): this month's date for a brand not read this month (already past = it goes at the next pass of the worker), next month's otherwise; null when off. `last_read_at` is NOT `brand_schedules.last_run_at` (that column is the due rule and is also a mark set by `enable` on a never-read brand): it is the `finished_at` of the latest `crawl` job with `params.scheduled` and status `done` of the brand (null if none; a manual, failed or cancelled read does not count). The audit screen shows `schedule.*` and `digest.*` with no author as « Lecture automatique ».

**Monthly e-mail (AXE-523, `cloud/digest.py`).** ONE e-mail per organization and per month, to its administrators who are allowed and want it (see « Who receives
it » below; at most 10), sent by the worker through `mailer.send` (Resend). *It says:*
(1) « Nouveaux visuels à traiter »: matches found since the last e-mail sent to the organization (35 days if none) on a visual
whose rights have expired (unlimited rights excluded) and with no decision yet. A match is dated by the later of the site
image's `first_seen` and the reference's `created_at`, not by `matches.created_at`, which every pass that rewrites a match sets
again (a re-indexed reference, a new threshold); (2) the visuals of the library expiring within 30 days, and (3) in 31 to 90
days (`SOON_DAYS`, `LATER_DAYS`: the only place of the two thresholds; a visual is in one list at most; expired ones are in
neither); (4) one sentence per brand whose last read looks incomplete (`crawl_runs.suspect`: nothing is marked removed), was
stopped by the month's page limit (`result.quota_reached` of the scheduled `crawl`) or is not finished (its scheduled read
is not `done`, or still due). Each list shows 20 lines per brand at most, then « et N autres », and a link to the page
(`NYRA_PUBLIC_URL` + `/o/<org>/b/<brand>/review` or `/library`). Every name is escaped in the HTML; no word on billing.
*When:* at least one brand has the monthly read on, the date of the latest brand has passed (a brand counts only if its
`brand_schedules` row existed before this month's date: `created_at`, which a switch off and on does not change; one switched
on after the date is read next month and mails next month), and no scheduled read of the
organization waits or runs, or 48 hours have passed since that date (the alert does not depend on the read: if it failed,
the mail says so); never twice a month. It leaves only if there is something to say; else the month is `skipped`.
*Once:* `claim` is one `INSERT ... ON CONFLICT DO UPDATE ... WHERE status = 'sending' AND <30 minutes old> AND attempts < 5
RETURNING`: two workers cannot both send. A claim left `sending` (worker dead, or a refusal) is taken again after 30 minutes,
5 attempts at most, always with the idempotency key `monthly:<org_id>:<YYYY-MM>`, so Resend never sends twice even if the
worker died between the send and the mark. After 5 attempts the worker gives up and warns once a day. Without
`RESEND_API_KEY` + `NYRA_MAIL_FROM` (or with a worker started without e-mail settings) nothing is claimed or marked and one warning an
hour is logged; without `NYRA_PUBLIC_URL` it leaves without links, with one warning an hour. *Which organizations:* `due_orgs`
filters in SQL by organization (the latest day of the month of its active brands has come, not handled yet), 200 per page in
`org_id` order, 5 pages at most per pass, and reads an organization whole. *Recovery:* if Resend answers 409
`invalid_idempotent_request` (same key, other body: an earlier attempt sent the mail and died before marking it, and the
content changed since), the mail has left: the month is marked `sent`, `digest.sent` carries `recovered: 1`.
More than 10 recipients: the first 10 (sorted) get it; `digest.sent` holds `recipients` (those who receive it) and `admins` (all the administrators), and a warning is logged. *Audit:* `digest.sent`
(counters only), `digest.skipped` (`reason`: `nothing`, `no_admin`, `no_recipient`), `digest.failed` (error type and attempt). *Switch:* `NYRA_SCHEDULER` (the same as the reads).
*Limits:* a reimport of the library gives the visuals new ids with no `reviews`: a visual already decided is « new » again; after
more than 24 hours spent in `sending` (the worker down for a day between a successful send and its mark) Resend's idempotency
key (valid 24 hours) no longer protects: the mail could leave twice; a visual that expires while already online appears in the 30-day list of the e-mail before, not as « new ».

**Who receives the monthly e-mail, and the notification settings (AXE-523, `cloud/routes/notifications.py`).** Two switches on
`memberships` (migration `20261025000035`), both `true` by default, for existing and new members: `monthly_email_allowed`
(the Nyra team's, per person and organization) and `monthly_email_wanted` (the person's own). ONE pure rule,
`digest.receives_monthly(role, allowed, wanted)`: role `admin` AND allowed AND wanted. A `client` never receives it,
whatever the switches. `digest.recipients` reads the members once and gives (administrators, sorted recipient addresses); the
read happens BEFORE the month is claimed. If nobody receives it the month is `skipped` with the reason `no_recipient`
(`no_admin` when the organization has no administrator at all; `nothing` comes first when there is nothing to say), without
an error. Without the migration the columns are missing: `recipients` raises `UndefinedColumn`, `digest.run` lets it through,
NOTHING is claimed or marked, and the worker logs one warning an hour naming migration `20261025000035`; apply it and the
month's e-mail leaves at the next pass. *Routes:* `GET /api/orgs/{org_id}/me/notifications` (any member) gives
`{monthly_email: {wanted, allowed, eligible}}` (`eligible`: the role is `admin`; `allowed` is shown, never changed, by the
person). `PUT` (the caller's own line only: the member is the session's, there is no member identifier in the path or the body)
takes `{monthly_email: bool}` (nothing else, 422) and writes `notifications.update` (`monthly_email`) in the same transaction,
only if the value changes (the same value: 200, no trace). `PUT /api/staff/organizations/{org_id}/members/{user_id}/notifications`
(platform admins only; the person must be a member of that organization, else 404) takes `{monthly_email_allowed: bool}` and
writes `notifications.staff_update` (the person as object, `monthly_email_allowed`), again only on a change. The back
office reads both flags in `GET /api/staff/users` (memberships). *Row level security:* `memberships` has an « org admins
manage memberships » policy (all commands), so an administrator could change their own row through Supabase's public API; the
trigger `guard_member_notifications` refuses, for the roles `anon` and `authenticated`, any change of
`monthly_email_allowed`, any change of another person's `monthly_email_wanted`, and a new row that does not start with both
on; the backend (service role / owner) is not touched; the function pins `search_path = ''`. *Known limit:* the trigger covers
neither `DELETE` nor `TRUNCATE`: an administrator of an organization (role `authenticated`, through Supabase's public API) can
delete a co-administrator's row that the team blocked and recreate it with the defaults, which lifts the block. They must be an
administrator of the SAME organization, the interface does not do it, and it is not access across organizations; to improve:
move the two booleans to a table without a « FOR ALL » policy, or cover `DELETE`. *Before the migration,* `GET /api/staff/users`
still works (`users_overview` reads both columns through `to_jsonb` with `true` as the default). *Screens:* Réglages > Notifications (a section of « Mon compte »; the
list of e-mails a person can switch off, one for now: « Résumé mensuel »; boxes disabled with a sentence for a non-administrator
and for « bloqué par l'équipe Nyra »; a sentence says the service e-mails, invitation and new password, are Supabase Auth's and
cannot be switched off); back office > Comptes, « administrateurs d'organisation »: a box « Autorisé » per person, and
« a choisi de ne plus le recevoir » when the person said no. The mail ends with the way to stop it, with a link to
`/o/<org>/b/<first brand>/settings?section=notifications` when `NYRA_PUBLIC_URL` is set.

**E-mails page of the back office (`cloud/email_registry.py`, `cloud/routes/staff_emails.py`).** `email_registry.ENTRIES` lists every e-mail of the
product: `key`, `label`, `description`, `sender` (`nyra`: sent by `mailer`; `supabase`: sent by Supabase Auth from `supabase/templates/`, never by Nyra) and
`sample()` giving `(subject, html, text)` from invented data (« Maison Exemple »). The monthly e-mail's sample is the real `digest.build` on made-up
data; the two Supabase samples are the template files with their `{{ .Variable }}` replaced by example values (the web image copies `supabase/templates`).
*Rule:* `mailer.send` and `mailer.deliver` take a required keyword `kind`, a key of the registry; `test_every_mail_kind_is_registered` reads the
syntax tree of every module and fails for a call whose `kind=` is not a literal key of the registry (or the `.key` of an entry taken from it), and for a
registered `nyra` e-mail that no code sends. *Routes* (platform admins only; the tests of `test_email_registry.py`): `GET /api/staff/emails`
(`configured` = key AND valid sender, `sender`, the entries with `can_send_test`; never the key), `GET /api/staff/emails/{key}/preview` (`subject`, `html`,
`text`; works when sending is not configured; 404 for an unknown key), `POST /api/staff/emails/{key}/test` (body empty or `{}`, any field is a 422): sends the sample, with
`[TEST]` in the subject and a banner above the body, to the address of the signed-in account ONLY, through `mailer.deliver` (no organization, so no client's daily
allowance is used). 400: Supabase e-mail or an account without an address; 503: not configured; 502: Resend's refusal or a timeout (message cut at 200 characters, no key);
429 with `Retry-After`: more than 10 tests in the last hour for the account (counted from its `email.test` audit rows, one bounded query; two tests started at the same
instant can pass together, by a few). The network call is made with no transaction open; the result is traced after it in its own short transaction:
`email.test` (no organization; `kind` and `result` = `sent` or `failed`, never an address or a content). The page (`/back-office/emails`) shows each e-mail
with « Aperçu » (the HTML in an `<iframe sandbox="">`: no script, no active link, plus the text version) and « M'envoyer un test » (not replayed on a 429).

**Ajouter un e-mail.** (1) Write the render function (pure, escaped, tested on invented data). (2) Add an `Entry` to `ENTRIES` in `cloud/email_registry.py`:
a short unique `key` (`[a-z_]+`), `label`, `description`, `sender` and a `sample()` that calls the render function on invented data (never a client's).
(3) Send it with `mailer.send(..., kind="<key>")` (an organization's mail, counted against its daily allowance) or `mailer.deliver(..., kind="<key>")`.
(4) Run `pytest backend/tests/test_email_registry.py`: without step 2 it fails, naming the file and the line. (5) The page « E-mails » of the back office shows it
with no further change; if Supabase sends it, `sender="supabase"` (preview only) and the template goes in `supabase/templates/`.

Job kinds: `crawl` (the chosen addresses of a brand, then compare),
`match`, `index` (fill in embeddings, mirror hashes and thumbnails —
queued after every upload, also backfills older rows; an administrator can start it from Réglages > Général > Maintenance, `POST .../jobs/index`), `report`, `locate` (checks one
reference against every stored site image to find its copies and crops; see `MATCHING.md`), `check` (reads again only the pages a person gives, 50 at most, `POST .../jobs/check`: no sitemap, no link followed, no comparison, and no crawl run, since a run would stand for a read of the whole site). A page that answered with another page (a redirection), failed, was blocked by robots.txt or whose read did not end is never counted as gone, and the job says how many were left out. A check counts once a full read of the site has finished; deploy the worker before the web process, so a page created after the migration always records when its read started.

## Organizations and brands

An organization is a client; it holds brands. A brand owns a library and
one or more sites (typically one per market: louis-xiii.com/us, /fr...),
and its references are only ever compared with images read on its own
sites. Exclusions and reports are per brand (the job queue runs one job per organization); settings and
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
| `cloud/audit.py` | The activity log (`audit_log`): `record` (called by each route that changes something, in the transaction of the change), `events` (reading, filtered and bounded), `purge` (12 months, batches). |
| `cloud/offboarding.py` | A client leaving: `delete_organization` (files, rows, accounts, trace) and `export_organization` (CSV, originals, manifest), behind `nyra cloud-delete-org` / `cloud-export-org`. Operator only, no route. |
| `cloud/routes/` (`session`, `audit`, `brands`, `members`, `library`, `collections`, `labels`, `occurrences`, `jobs`, `matches`, `site_images`, `logos`, `reports_settings`, `schedule_settings`, `notifications`, `staff_emails`) | The API routes, one module per domain, each exposing a `router`. |
| `cloud/routes/interface.py` | The built interface (single-page app), gzip and security headers, error handlers. |
| `cloud/presence.py` | Where a visual is still online, page by page: `still` (seen by the last finished read of the site), `gone` (the page was read again with success and the image was not on it), `unknown` (the page was not read again, or not since `image_pages.last_seen` and `pages.links_tracked` exist). Counts per site version and section, and the pages; `GET .../visuals/{reference_id}/presence`. Images and links are never deleted when they disappear from a site, so nothing is stored: the state comes from the dates. While the last finished read of a site is **suspect** (`crawl_runs.suspect`, set when the read ends, see `MATCHING.md`), no page of it is `gone`. `replaced_references` lists the visuals replaced, which the home screen and the report leave out of "expired and online". |
| `cloud/worker.py` | The job runner. |
| `cloud/jobs.py` | The queue (enqueue, claim, heartbeat, cancel, reap). |
| `cloud/schedule.py` | The monthly read (`brand_schedules`): `is_due` (pure), `enable` / `disable`, `enqueue_due` (called by the worker every 5 minutes), the `NYRA_SCHEDULER` switch. |
| `cloud/email_registry.py` | Every e-mail of the product with a sample of invented data (preview and test mail of the back office), and the `kind` every `mailer` call must name. |
| `cloud/digest.py` | The monthly e-mail (`monthly_digests`): `gather` (reads), `build` (pure: subject, HTML, text), `due_orgs` / `is_ready` (when), `claim` (atomic), `run` (called by the worker right after `enqueue_due`). |
| `cloud/store.py` | Postgres + Storage behind the store interfaces. |
| `cloud/db.py` | Pooled Postgres access, one query per screen (no N+1). |
| `cloud/storage.py` | Supabase Storage; signs URLs in batches and remembers a bounded number of them, so an image keeps the same URL while it is valid. |
| `cloud/auth.py` | JWT verification (HS256 or JWKS) and membership/role checks. |
| `cloud/ratelimit.py` | Limitation de débit de l'API : un seau à jetons par utilisateur vérifié, lectures et écritures séparées (429 + `Retry-After`), branchée comme dépendance de toute l'application ; les contrôles de santé n'y sont jamais soumis. Valeurs : `NYRA_RATE_READ_PER_MIN` (300), `NYRA_RATE_WRITE_PER_MIN` (120). |
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
- **Who did what is kept** (`audit_log`, 12 months). Each route that changes something
  writes a row itself (`scope.audit(conn, "library.delete", …)`), on the connection of the change:
  both commit or neither does, so an action never happens without its trace and a failing trace
  is an error the person sees, not a silent gap. Explicit calls rather than a middleware on the
  verbs: a middleware runs after the response is built (it cannot cancel the action) and knows a
  URL, not "this image was ignored". `backend/tests/test_audit.py` enumerates every route that
  changes something and fails for one that does not audit (or is not on a short, justified list), so
  a future route cannot forget. A row holds identifiers and counters, never content (`record` refuses a
  long text, a list, a file name). Read by the organization's admins (`GET /api/orgs/{org_id}/audit`) and
  by the Nyra team (`GET /api/staff/organizations/{org_id}/audit`).
- **Data leaves or goes only by operator command**: `nyra cloud-export-org` and `cloud-delete-org`
  (exact name to type, `--dry-run`, trace in the logs and `deleted_organizations`); no route does either.
  The cascade covering every table is checked on the catalog by a test. Backups of Storage and
  restoration are a procedure (`DEPLOYMENT.md`), not code.
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
