# Deployment

Two containers built from one `Dockerfile`, both pointed at the same
Supabase project:

| Target | Command | Contains | Size |
|---|---|---|---|
| `web` | `nyra serve` | API + built interface | small (no Chromium, no torch) |
| `worker` | `nyra worker` | Chromium, CPU torch, the CLIP weights | large |

```bash
docker compose up -d --build
docker compose up -d --scale worker=3   # more organizations processed in parallel
```

Any host that runs containers works (a VPS with compose, Fly.io, Railway,
Render, Kubernetes). Terminate TLS in front of `web` (the platform's load
balancer, or Caddy/nginx). `web` can run several instances; nothing about
a job lives in its memory.

## DigitalOcean

One Droplet runs everything (web, worker, HTTPS, restart of a hung worker); Postgres, Auth and
Storage stay on Supabase.

1. Create a Droplet, Ubuntu 24.04 with the Docker image from the Marketplace, **4 GB / 2 vCPU**
   (Basic, $24/month). Add a DNS A record for your domain pointing at it.
2. `git clone` the repository, copy `.env.example` to `.env`, fill it in, set `NYRA_DOMAIN`
   and `NYRA_PUBLIC_URL=https://<NYRA_DOMAIN>`.
3. `docker compose -f docker-compose.yml -f docker-compose.droplet.yml up -d --build`
   (the first build is long: Chromium, torch and the CLIP weights).
4. Do the Supabase setup below, then check `https://<NYRA_DOMAIN>/api/healthz`.
5. Firewall (Networking > Firewalls): inbound 22, 80, 443 only.

`autoheal` reads the Docker socket to restart containers marked `unhealthy`; drop it from
`docker-compose.droplet.yml` if you would rather not give a container that access.

Updating a running Droplet: `git pull`, then the same `docker compose … up -d --build`. If `git pull` refuses
because a file was edited on the Droplet, look at `git diff <file>` first; `git stash` sets the edit aside.

The Droplet keeps no state (no volume but Caddy's certificates; the database, Auth and files are on
Supabase), so it is rebuilt from git and `.env`. Weekly backups of the Droplet are optional; keep a copy of
`.env` elsewhere. A Droplet cannot be resized to a plan with a smaller disk (the 4 GB plan has 80 GB, the
2 GB plans 50–60 GB): going down means creating a new Droplet, pointing the DNS at it, then deleting the old one.

**Measured on 18 October 2026** (`docker stats` on the 4 GB Droplet, during a crawl with CLIP embedding; two
readings, the final peak was not recorded): `worker` 835 MiB then **1.88 GiB**, 150 % of a CPU and 151
processes; `web` 150 MiB; `caddy` and `autoheal` under 15 MiB. About 2 GiB in use by the containers, before the system (the limits below add up to more, 3.2 GiB: they are ceilings).
A 2 GB Droplet does not fit; 4 GB leaves room for one job at a time.

Monthly cost, Basic Droplet prices of October 2026 (check
[digitalocean.com/pricing](https://www.digitalocean.com/pricing/droplets)):

| Setup | Droplet | Backups (weekly, +20%) | Total |
|---|---|---|---|
| Small: 1 worker, a few brands | 4 GB / 2 vCPU, $24 | $4.80 | about $29 |
| Comfortable: `--scale worker=2` | 8 GB / 4 vCPU, $48 | $9.60 | about $58 |
| Split: web apart from the worker | 1 GB, $6 + 4 GB, $24 | | about $30 |

Supabase is billed separately. App Platform also works (web service on `--target web`,
worker component on `--target worker`) but the worker needs at least the 2 GB container ($25) and
more comfortably the 4 GB one ($50), plus the web service ($5-10): roughly twice the Droplet for
the same work. The Droplet is the recommended path.

## Resources

Limits per container, set in `docker-compose.yml` (and `docker-compose.droplet.yml` for Caddy and autoheal).
**They are starting values, not measured ones**: the worker's final peak was never recorded (AXE-530), so
confirm them with the measure and the test below. Each can be overridden in `.env`.

| Container | `mem_limit` (= `memswap_limit`, no swap) | `cpus` | Why |
|---|---|---|---|
| `worker` | `2560m` (`WORKER_MEM_LIMIT`) | `1.5` (`WORKER_CPUS`) | Measured 1.88 GiB during a crawl with CLIP (~1 GB torch + Chromium); `shm_size: 1gb` counts inside it. Leaves half a vCPU to web and Caddy. |
| `web` | `512m` (`WEB_MEM_LIMIT`) | `1` (`WEB_CPUS`) | 150 MiB measured (the 18 October reading above). |
| `caddy` | `128m` (`CADDY_MEM_LIMIT`) | none | Under 15 MiB, same reading. |
| `autoheal` | `64m` | none | Under 15 MiB, same reading. |

On the 4 GB Droplet (about 3.8 GiB usable): 2560 + 512 + 128 + 64 MiB = 3.2 GiB leaves about 0.6 GiB for the system. Without limits, the kernel's
out-of-memory killer picks the biggest process of the whole machine, which can be `web` or Caddy. With them, only the
worker is killed (exit 137), `restart: unless-stopped` starts it again, and its job is put back in the queue
(see "Staying up"). The limits are per container: `--scale worker=2` needs the 8 GB Droplet and
`WORKER_MEM_LIMIT` adjusted. If only Chromium (a child process) is killed, the job fails with the browser's message
instead of the worker restarting.

**Two global caps on what runs at once** (integers >= 1, default **1** each, read by every worker at each poll; an invalid
value is logged once as a warning and the default is used; edit `.env` and restart the workers to change them):
`NYRA_MAX_HEAVY_JOBS` for the jobs that read sites (`crawl` and `check`, which load Chromium, and `locate`), and
`NYRA_MAX_MODEL_JOBS` for the jobs that analyse images (`index` and `match`, which load the CLIP model and the
library's vectors). A job only takes a place of its own kind, so a long crawl does not hold back another customer's
indexing; `report` has no cap. The excess stays queued (nothing is refused or lost) and starts when a place frees up,
the customer who started a job least recently first. Only a running job whose heartbeat is less than 3 minutes old
holds a place: the row a dead worker leaves behind frees it. **Keep 1 until the peak is measured**: record the worker's
memory (`docker stats`) during a crawl, and during an indexing or a comparison of the largest library, then check that
N times that peak (not the 2560 MiB limit times N) fits in the machine.

**The caps change nothing with a single worker.** A worker process runs ONE job at a time (`run_once`), and
`docker-compose.yml` starts one `worker` container. While it runs a 12-hour crawl for one customer, no other job
starts, indexing included, whatever the caps say. Another customer's indexing only moves during a crawl when at least
two workers run (`--scale worker=2`), and the 4 GB Droplet (worker limited to 2560 MiB, about 3.2 GiB of limits in
all) probably cannot host a second one without changing size. The caps are what keeps two workers from reading the same
sites or loading the model twice, once a second worker exists; until then jobs simply wait their turn in the queue, the
customer who started a job least recently first. Independently of the caps nothing is refused, and two jobs never read the same domain (host without `www.`) at the same time, whatever the customers: the second `crawl` or `check` waits for the first to end. `.env.example` should
list `# NYRA_MAX_HEAVY_JOBS=1` and `# NYRA_MAX_MODEL_JOBS=1`.

**Monthly read** (`cloud/schedule.py`). The worker looks every 5 minutes for the brands whose monthly read is due and
queues it (see `ARCHITECTURE.md`, « Monthly read »). `NYRA_SCHEDULER=0` (or `false`, `off`) stops it; unset, `1`, `true`
or `on` keep it on; any other value is logged once as a warning and the default (on) is used. Edit `.env` and restart the
worker. It does nothing until a brand's schedule is switched on (the table `brand_schedules` is empty at first). Limit: the
worker does one job at a time and does not look for due reads while it runs one, so a monthly read can start a few hours
late (never earlier than it could have started anyway). Migration `20261024000033` creates the table: **apply it by hand
on Supabase before deploying** (SQL editor, as for every migration). The reverse order is harmless: the table is empty and
inactive, an old worker ignores it, and a new worker without the table logs one warning an hour (« apply migration 20261024000033 », no trace, so no Sentry
event every 5 minutes) until the migration is applied; nothing else is affected. Any other failure of the pass keeps its full
error log.

**Monthly e-mail** (`cloud/digest.py`, see `ARCHITECTURE.md`, « Monthly e-mail »). Right after queueing the reads, the worker
sends each organization's e-mail once a month, to its administrators. *Before deploying:* apply migration
`20261025000034_monthly_digests.sql` on Supabase (SQL editor; it creates `monthly_digests`, idempotent, empty at first). The
reverse order is harmless: the new worker logs one warning an hour (« apply migrations 20261024000033 and 20261025000034 »,
no trace) and sends nothing. *Variables:* `RESEND_API_KEY` **and** `NYRA_MAIL_FROM` together, plus `NYRA_PUBLIC_URL` for the links
(the worker reads the same `.env` as the web process; a wrong `NYRA_MAIL_FROM` stops the web process at start, but NOT the
worker: it logs an error « NYRA_MAIL_FROM invalide : les e-mails mensuels sont désactivés » and starts without e-mail, so the jobs
keep running; fix the variable and restart the worker. Without `NYRA_PUBLIC_URL` the e-mails leave without links, with one warning an hour). Without them nothing is sent, nothing is marked sent, and the worker logs one warning an hour; set them and restart
the worker and the month's e-mail leaves at the next pass. `NYRA_SCHEDULER=0` also stops it. A refused or failed send is tried
again after 30 minutes, 5 times a month at most, then abandoned with one warning a day (`abandoned after 5 attempts`).
*Who receives it:* administrators whom the Nyra team allows (back office > Comptes, box « Autorisé ») and who want it
(Réglages > Notifications), both on by default. *Before deploying:* apply migration `20261025000035_member_notifications.sql`
on Supabase (two columns on `memberships`, a trigger; idempotent, nothing changes for anyone until a box is unticked). It must
come BEFORE the code: without the columns the worker logs one warning an hour (« columns monthly_email_allowed / monthly_email_wanted
of memberships are missing, apply migration 20261025000035 »), claims and marks nothing and sends nothing (the mails leave at the next pass once
it is applied), the back office list of accounts (`/api/staff/users`) and the notification routes answer 500.
To check the sending by hand, from a Python prompt on the server (the key typed at the prompt, never in a command line):
`from nyra.cloud import mailer; mailer.deliver(key, sender, ["you@example.com"], "Test", "<p>Test</p>", "Test")`. To resend a
month's e-mail after fixing a cause, delete its row (`delete from monthly_digests where org_id = '...' and month = '2026-11-01'`);
within 24 hours of a first send Resend treats the same key as the same mail and does not send it again, so wait a day or change nothing.

## Supabase setup, once

1. **Migrations**: apply `supabase/migrations/` in order (see
   `supabase/README.md`).
2. **Authentication > Providers > Email**: turn off *Allow new users to
   sign up*. Access is by invitation only; the backend has no sign-up
   route, and this closes the direct path through the public anon key.
3. **Authentication > URL Configuration**: *Site URL* = your public
   address; add `<site>/login` and `<site>/set-password` to the
   redirect URLs (invitation and password-reset links land there). Keep
   `<site>/connexion` and `<site>/mot-de-passe` as well: links e-mailed
   before October 2026 still point there, and the app redirects them.
4. **Authentication > Emails > Templates**: paste `supabase/templates/recovery.html`
   (*Reset password*) and `supabase/templates/invite.html` (*Invite user*) with
   the subjects « Nouveau mot de passe — Nyra » and « Votre accès à Nyra ». The
   templates carry no image: mail clients block remote images from unknown senders.
   Supabase's own sender is limited to a few e-mails an hour and to your team's addresses, so a client's
   invitation would not arrive: set **Authentication > Emails > SMTP Settings** to Resend (host
   `smtp.resend.com`, port `465`, user `resend`, password a Resend API key limited to sending, sender an
   address of a domain verified in Resend), and raise the e-mail rate limit under *Rate Limits*.
5. **Environment**: copy `.env.example` to `.env` and fill it in, including
   `NYRA_PUBLIC_URL`.
6. **First organization**:

   ```bash
   docker compose run --rm web nyra cloud-provision-org \
     --name "Client" --slug client --admin-email admin@client.com
   ```

## Upgrading an existing deployment

Apply new migrations before starting new containers. From the
`thumbnails_and_flip_hashes` migration on, existing references and site
images are missing thumbnails and mirror hashes; the worker fills them in
with an `index` job. To run one immediately for an organization:

```sql
insert into jobs (org_id, brand_id, kind) select o.id, b.id, 'index' from organizations o join brands b on b.org_id = o.id where o.slug = 'client';
```

Migration `20261021000032` (one running job per organization; it also adds the partial index
`jobs_org_active_idx` that `jobs/current` reads) fails, with a message and no change, if an organization has two
running jobs when it is applied (the earlier rule allowed one per brand). **The order matters**, in both directions:
a new worker against the old per-brand index can start two jobs of one organization at once (and the migration then
fails), and an old worker against the new index retries the same queued job of a busy organization in a loop, which
holds up every organization. So: (1) stop the workers; (2) check that nothing runs
(`select org_id, count(*) from jobs where status = 'running' group by 1 having count(*) > 1` must return no row;
let the jobs end, or wait 3 minutes for `reap_stale` to requeue them); (3) apply the migration; (4) deploy the web
process; (5) start the new workers.

## Logs and health

Both processes log to stdout (`NYRA_LOG_FORMAT=json` for one JSON object
per line; `SENTRY_DSN` to report server exceptions, `SENTRY_BROWSER_DSN` to
measure and report errors in the browser, with `NYRA_ENV`, `NYRA_RELEASE` and
`SENTRY_TRACES_SAMPLE_RATE`; see `OBSERVABILITY.md`). `web` exposes `GET /api/healthz` (checks
the database) and the image declares a Docker `HEALTHCHECK`. A job that
fails shows its error in the interface and in `jobs.error`; the full
traceback is in the worker's log.

## Staying up

- **A process that exits** is restarted by the platform (`restart: unless-stopped` in compose).
- **A job whose worker died** (crash, redeploy, restart) is noticed after 3 minutes without a
  heartbeat and **put back in the queue**, a crawl resuming where it was, up to 2 times; after that it
  is marked failed, so a job that kills its worker every time cannot loop. A job whose stop was
  requested is not restarted.
- **A worker killed for memory** (container limit, exit 137, no chance to write a message): restarted by
  `restart: unless-stopped`; its job has no heartbeat for 3 minutes, goes back to the queue, and after the
  2 retries fails with « le worker s'est arrêté 3 fois de suite…, le plus souvent faute de mémoire ou à cause d'un redémarrage du serveur. Relancez-la
  en réduisant le nombre de pages ou d'images… ».
- **A hung worker** restarts itself, with no component reading the Docker socket: a watchdog thread of the
  worker exits (code 70) when its alive file has not been touched for 5 minutes (`WATCHDOG_SECONDS`), and
  `restart: unless-stopped` restarts it. This works with plain `docker compose` and on any platform. It covers a
  main loop stuck between jobs (a database call that never returns). It cannot run if the whole interpreter
  is frozen (a native call holding the GIL), and during a job the heartbeat thread keeps the file fresh: a job
  stuck inside Chromium or torch is stopped by its maximum duration (below). The image's `HEALTHCHECK` still
  reports `unhealthy` for platforms that act on it (Fly, Railway, Render, Kubernetes), and the Droplet's
  `autoheal` service restarts any container turned `unhealthy`, `web` included. **Security**: autoheal mounts
  `/var/run/docker.sock` read-write, which is root on the Droplet if that container is compromised; the image
  is pinned by tag (`1.2.0`, not by digest: a re-published tag would be pulled) and it exposes no port. Drop it from `docker-compose.droplet.yml` if you accept that
  `web` is not restarted when hung (the worker no longer needs it).
- **Maximum duration per kind of job** (`JOB_LIMITS` in `cloud/worker.py`; **starting values** to adjust from the
  durations in the Statistiques page): `crawl` 12 h (all the brand's sites, then the comparison), `index` 6 h, `match`
  1 h, `locate` 1 h, `report` 30 min, `check` 30 min. Each can be set in `.env` with `NYRA_JOB_LIMIT_<KIND>_MIN`
  (whole minutes, 1 to 1440; anything else is ignored with a warning in the log). Past it, the job is marked failed
  (its crawl runs closed) with « … a dépassé sa durée maximale de N min. Le travail déjà enregistré est conservé :
  relancez-la, ou réduisez … » and the worker exits to restart (a hung job cannot be stopped from a thread). It is
  not retried, and only if that very run is still running (a job that ended at that moment is left alone). A
  crawl started again resumes where it stopped. The database calls of the heartbeat and of this failure give up after
  15 s, and the exit happens after 20 s whatever the database does.
- **Disk**: the worker removes Chromium's leftovers (`playwright_chromiumdev_profile-*` and similar, in which nothing was
  written for 10 minutes) from the temp directory at start and after every job, even a failed one, and trims its image cache
  to `NYRA_IMAGE_CACHE_MB` (2048) at start (and every 200 writes). Docker's own images
  and logs are not cleaned: run `docker system prune` from time to time. A job that hits a full disk or
  memory error fails with a message that says so.
- **Supervision alerts** (`backend/nyra/cloud/alerts.py`): four thresholds are judged on the latest readings and the
  queue. They are shown at the top of the Serveur page of the back office (« Tout va bien » when none is crossed) and
  listed in `alerts` of `GET /api/staff/server` and of `GET /api/workerz`, as `{code, severity, message}` (no
  organization, brand or user in them). Nothing is sent yet (no e-mail, no Slack): the notification to come reads
  that list. Each can be set in `.env`; a value that is not a whole number in range is ignored with a warning in the log.

  | Code | Severity | Raised when | Default | Setting (range) |
  |---|---|---|---|---|
  | `worker_absent` | critical; warning when the metrics table holds no worker at all | every worker has been silent for over N seconds | 300 s (a worker publishes every 15 s from a thread, jobs or not, but a deployment recreates the container with a new host name and its first reading comes 15 s or more after the start) | `NYRA_ALERT_WORKER_SILENT_SECONDS` (30 to 3600) |
  | `queue_stalled` | critical | a queued job has waited over N minutes while no job runs anywhere | 5 min (an idle worker claims a job within seconds) | `NYRA_ALERT_QUEUE_MINUTES` (1 to 1440) |
  | `disk_full` | critical | the machine's disk is N % used | 80 % | `NYRA_ALERT_DISK_PERCENT` (1 to 100) |
  | `worker_memory` | warning | a worker uses N % of its memory limit | 85 % | `NYRA_ALERT_WORKER_MEMORY_PERCENT` (1 to 100) |

  The memory is judged against the container's own limit (`WORKER_MEM_LIMIT`, 2560m in `docker-compose.yml`, read from
  the cgroup); a worker with no limit is judged against the machine's memory. 85 % of 2560m is 2.1 GiB, above the
  1.88 GiB (73 %) measured during a crawl with CLIP: past it the worker is close to being killed. The `web`
  service judges the thresholds (both services read `.env`): restart it after changing one. The Serveur table
  still marks a worker « muet » after a fixed minute (a table row, not an alert), whatever `NYRA_ALERT_WORKER_SILENT_SECONDS`.
  **An environment without a worker** (development, CI, a table purged after the 24 h the readings are kept) has no
  worker row: the alert is then a warning, never a 503. A worker dead for over a day is still caught by `queue_stalled`
  as soon as a job waits.
- **No worker at all**: point an uptime monitor (UptimeRobot, Better Stack...) at `GET /api/workerz`.
  It answers 503 while a **critical** alert is on, and 200 `"status": "ok"` otherwise. `status` and `problems` keep their
  historical words, unchanged: `"no worker"` (a job waiting for nothing, or every worker silent for over 5 minutes) and
  `"disk almost full"`; the new codes are only in `alerts`. A `worker_memory` warning does not make it fail;
  `queued_wait_seconds` and `disk_percent` are in the answer too. Most monitors alert on the change of state (up to
  down), so a deployment that stays under 5 minutes of silence stays green. Keep
  `GET /api/healthz` for the web process. The interface already tells an administrator that a task
  waits for a worker after a minute.

## Manual check still to do on the Droplet

The limits above are untested values. Before relying on them:

1. During a crawl with CLIP, note the worker's peak (`docker stats`, or the Serveur page of the back office,
   every 15 s) and set `WORKER_MEM_LIMIT` to about 30 % above it, as long as the sum stays under 3.3 GB.
2. Force an overrun (for example `WORKER_MEM_LIMIT=700m` in `.env`, `docker compose up -d`, start a crawl): the
   worker must be killed (`docker inspect -f '{{.State.OOMKilled}}' <worker>`), restart on its own, and the job
   go back to the queue after 3 minutes. **While this happens, `/robot` and the sign-in must stay online** and
   `docker stats` must show `web` and Caddy untouched. After 3 attempts the job must fail with the memory message.
3. Put `WORKER_MEM_LIMIT` back, and check `docker compose ps` shows `web`, `worker` and `caddy` healthy.

## Security checklist

- `.env` holds the service-role key: keep it out of synced folders,
  images (`.dockerignore` excludes it, as does `.gitignore`) and screenshots; rotate it if in
  doubt (procedure below).
- Public sign-up disabled in Supabase (step 2 above).
- Members reviewed every quarter (next section).
- Never set `NYRA_ALLOW_PRIVATE_HOSTS` on a server: it turns off the
  guard that keeps crawls away from private addresses and cloud metadata.
- `crawl.max_pages_limit` in `config.yaml` bounds the cost of any single
  crawl, whatever an organization asks for.
- The `quota` section of `config.yaml` bounds what one organization uses over time (pages read per
  month, brands, addresses per brand, library, storage, members, invitation e-mails per day; starting values
  to be confirmed, see `COUTS_PRODUCTION.md`). Changing the file takes effect on the next request or job, no
  restart. The Nyra team overrides a limit for one organization in the back office (Usage); an organization
  cannot. Apply migration `20261020000030` before deploying the web and the worker: the counters are written
  by the worker, read by the web.
- Invitation e-mails go through Supabase Auth and your SMTP (Resend): `quota.emails_per_day` keeps one
  organization from using the whole daily allowance of the provider (100 a day on Resend's free plan).
- Supabase rate limits set (below), once, by hand.
- GitHub: *Settings > Code security*: turn on secret scanning and push protection, Dependabot alerts.
  Not checked from the code; the CI covers the same ground with gitleaks and the audits.

### What CI checks

- `pip-audit` (job `backend`): any known vulnerability in the installed Python dependencies fails the job
  (the Python advisory database has no usable severity). `pyproject.toml` does not pin versions, so the first run on
  GitHub may be red because of a package not audited here (torch, open_clip, playwright): upgrade it if a fix exists,
  else ignore that one advisory. To accept one, add `--ignore-vuln <ID>` to the
  step in `.github/workflows/ci.yml` with a comment saying why and when to look again. Never remove the step.
- `npm audit --audit-level=high` (job `frontend`): a **high** or critical advisory fails; moderate and low ones
  are left to Dependabot (`.github/dependabot.yml`, weekly, security updates grouped, 5 PRs open at most).
- `gitleaks` (job `secrets`): the whole history (`fetch-depth: 0`), binary pinned to a version and a SHA-256.
  Default rules, plus one false positive (`jwt_secret=SECRET` in a test: a variable name, no value) allowed by its
  exact fingerprint in `.gitleaksignore`, not by a rule. A real finding means the secret is burnt: rotate it (below)
  *then* clean up, rewriting history alone is not enough. **Limit:** the job reads `.gitleaksignore` from the PR's own
  tree, so a PR can add a line there and turn the job green. To decide: protect that file (and `.github/`) with a
  `CODEOWNERS` entry and required review. Not created here.
- Locally: `gitleaks git --redact --no-banner .` and `cd frontend && npm audit --audit-level=high`.

### Secrets: where they are

All are environment variables, read by `web` and `worker` from the server's `.env` (`env_file: .env` in
`docker-compose.yml`; Caddy reads the same file for `NYRA_DOMAIN`). On Vercel they are project variables.
Complete list (grep of `os.environ` in `backend/nyra`):

| Variable | Secret? | Used by | Rotate how |
|---|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | **yes, full access, RLS bypassed** | web, worker | 1 |
| `SUPABASE_JWT_SECRET` | **yes**, only with the legacy HS256 secret | web | 2 |
| `DATABASE_URL` (carries the database password) | **yes** | web, worker | 3 |
| `SENTRY_DSN` | low (can only send events) | web, worker | 4 |
| `SENTRY_BROWSER_DSN`, `SUPABASE_ANON_KEY` | public by design (sent to the browser) | web | only if abused |
| Resend API key (invitations) | **yes**, but **not in `.env`**: it is the SMTP password in Supabase | Supabase Auth | 5 |
| `RESEND_API_KEY` (the summaries and alerts Nyra sends itself) | **yes**, never logged | web, worker | 6 |

Not secrets: `NYRA_MAIL_FROM` (the sender, `Nyra <nyra@your-domain.com>` or `nyra@your-domain.com`, on a domain verified in Resend;
checked at start: a wrong form stops the server with a clear message. Set it together with `RESEND_API_KEY`: with only one of the two the server starts, a key alone logs one warning, and sending a mail raises `MailNotConfigured`), `SUPABASE_URL`, `NYRA_PUBLIC_URL`, `NYRA_DOMAIN`, `NYRA_ENV`, `NYRA_RELEASE`, `NYRA_LABELS`,
`NYRA_LOG_FORMAT`, `NYRA_LOG_LEVEL`, `SENTRY_TRACES_SAMPLE_RATE`, `NYRA_DB_POOL_MAX`, `NYRA_IMAGE_CACHE`,
`NYRA_IMAGE_CACHE_MB`, `NYRA_WORKER_ALIVE_FILE`, `NYRA_ALLOW_PRIVATE_HOSTS`, `NYRA_RATE_READ_PER_MIN`,
`NYRA_RATE_WRITE_PER_MIN`. Also keep the Droplet's SSH
access and the DigitalOcean and GitHub accounts behind 2FA.

### Rotating a secret, step by step

Rule for each one: **create the new value, deploy it, check, and only then revoke the old one.** Do one
secret at a time, in a quiet hour. If the old value leaked, go faster: revoke at step 5 even if the check fails
and fix forward.

1. Create the new value (sections 1 to 5 below). Write it in your password manager, never in a chat or a ticket.
2. On the server, edit `.env` (`ssh` to the Droplet, `cd` to the checkout; keep a copy of the old file in the
   password manager until the check passes).
3. Restart so the new value is read (containers read `.env` only at start):

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.droplet.yml up -d --force-recreate --scale worker=N worker
   docker compose -f docker-compose.yml -f docker-compose.droplet.yml up -d --force-recreate web
   ```

   (`N` = the number of workers you run now; without `--scale` compose goes back to one.) `worker` first: Docker sends SIGTERM and waits `stop_grace_period: 30s`; a job still running then is picked up
   again by the new worker after 3 minutes without a heartbeat (see *Staying up*), nothing is lost. `web` next:
   there is one instance behind Caddy, so expect a gap of a few seconds (Caddy answers 502, the interface retries on
   the next click). Not tested as a zero-downtime switch; if that matters, run two `web` instances first.
4. Check (all of these, in this order):

   ```bash
   curl -fsS https://$NYRA_DOMAIN/api/healthz      # {"status":"ok"}: the database answers with the new DATABASE_URL
   curl -fsS https://$NYRA_DOMAIN/api/workerz      # {"status":"ok",...}: no job waits for a dead worker
   docker compose logs --tail=50 web worker        # no 401/403 from Supabase, no "password authentication failed"
   ```

   Then sign in in a private window (checks the JWT secret or keys), open a brand's library (thumbnails load:
   the service-role key signs the Storage URLs), and run a test job on a test organization:

   ```sql
   insert into jobs (org_id, brand_id, kind) select o.id, b.id, 'index' from organizations o join brands b on b.org_id = o.id where o.slug = 'test-org';
   select status, error from jobs order by created_at desc limit 1;   -- 'done' within a few minutes
   ```
5. Revoke the old value (below), then check `/api/healthz` and a sign-in once more.

**1. Service-role key.** Supabase > *Project Settings > API Keys*. With the new key system: create a new secret
key, put it in `SUPABASE_SERVICE_ROLE_KEY`, deploy, check, then delete the old key. With the legacy keys, the
service-role key only changes with the legacy JWT secret (see 2), and so does the anon key: plan for both.
Search the places it may have been copied (synced folders, screenshots, shell history, other `.env` copies).

**2. JWT secret / signing keys.** Which one depends on *Project Settings > JWT Keys*:
asymmetric keys (RS256/ES256): `SUPABASE_JWT_SECRET` is empty and the backend checks tokens against the public JWKS.
Rotate the key in Supabase (create a standby key, make it current, later revoke the old one) and **restart `web`**:
the backend caches the keys it fetched until it restarts (`cache_keys=True`), so a token signed by the new key is refused
until then. Legacy secret (HS256): changing it signs everyone out and re-issues the anon and service-role keys (1); update
`SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` together, restart both, and tell users to sign in again.

**3. Database password.** Supabase > *Database > Settings > Reset database password*. Put it in `DATABASE_URL`
(percent-encode special characters), restart `worker` then `web`. The old password stops working at once, so
there is no overlap: expect failed requests until both restarts are done, and check `/api/healthz`.

**4. Sentry DSN.** Sentry > the project > *Settings > Client Keys*: create a key, update `SENTRY_DSN`
(and `SENTRY_BROWSER_DSN` for the browser project), restart, trigger a test error and see it arrive; then disable the old key.

**5. Resend key.** Resend > *API Keys*: create a key limited to sending, paste it in Supabase > *Authentication >
Emails > SMTP Settings* (password), send yourself a password-reset e-mail from the sign-in page and check it arrives; then delete the old key in Resend.

**6. Resend key for Nyra's own e-mails.** Resend > *API Keys*: create a second key limited to sending (one key per use,
so that revoking one does not break the other), put it in `RESEND_API_KEY` in `.env` **together with** `NYRA_MAIL_FROM` (an address of a domain verified in Resend; without it mail stays off and the server logs a warning), restart `web` and `worker`, then
send yourself a test mail from a Python prompt on the server (`from nyra.cloud import mailer; mailer.deliver(key, sender, ["you@example.com"], "Test", "<p>Test</p>", "Test")`,
typed at the prompt, not in a command line) and check it arrives; then delete the old key in Resend. Every mail
counts against the organization's `quota.emails_per_day`, and a failed send gives its place back. A caller that may retry after a failure (the monthly summary) passes `idempotency_key="monthly:<org_id>:<YYYY-MM>"`, so Resend never sends the same mail twice within 24 hours.

*Not yet done:* this procedure has been written from the code and the product documentation, not played. The team
should run it once on a test environment (separate Supabase project, a Droplet or a local compose) and fix this page where it differs.

### Sign-in attempts and rate limits

*To confirm on the current Supabase dashboard:* the menu names used on this page (*Authentication > Providers*,
*Rate Limits*, *Attack Protection*, *Emails > SMTP Settings*, *Project Settings > API Keys / JWT Keys*), the default and
recommended numbers below, and what happens to the keys when the legacy JWT secret changes. They come from the product
documentation as known when this was written, not from a check of the dashboard.

**How sign-in works.** The browser talks to Supabase Auth directly with the public anon key (password sign-in,
password reset, invitation links). Nyra's backend has no sign-in or sign-up route; it only checks the JWT that Supabase
issued. The routes reachable without a token are `GET /api/healthz`, `GET /api/workerz`, `GET /api/auth/config`
(public settings of the page) and the static interface; everything else needs a valid token and a membership. So the
password-guessing surface is Supabase Auth, and limiting it is done there.

**To set by hand in Supabase** (*Authentication > Rate Limits*; check the values the dashboard shows today, they have
changed over time). Recommended for a closed, invitation-only product with a handful of users:

| Setting | Recommended | Why |
|---|---|---|
| Sign-ups and sign-ins (per 5 min per IP) | 10 | a person types a wrong password a few times, a script does hundreds |
| Token verifications (OTP / magic link / invitation, per 5 min per IP) | 10 | same reasoning |
| E-mails sent (per hour, needs custom SMTP) | 30 | invitations and resets of a few clients; a flood of reset e-mails is also a cost and a spam risk |
| Token refreshes (per 5 min per IP) | keep the default | a user behind a company NAT refreshes often; lowering it signs people out |

Also in *Authentication*: *Attack Protection* (CAPTCHA, Turnstile or hCaptcha, on sign-in and reset: the strongest step
against guessing, it adds a widget to the page, a decision for the team), *Providers > Email* (minimum password length
8 or more; leaked-password protection on plans that have it), and no sign-up (step 2 of the setup).

**Rate limit of the API (per signed-in user).** `web` refuses a person who sends too many requests in a minute with
`429` and a `Retry-After` header (a sentence in French in `detail`; the interface shows it like any other error). A token
bucket per user, in the process's memory, with a burst as large as the minute's allowance:

| Variable | Default | Counts |
|---|---|---|
| `NYRA_RATE_READ_PER_MIN` | `300` | `GET`/`HEAD`/`OPTIONS` of one user (the interface polls `jobs/current` every 2 s: 30 a minute) |
| `NYRA_RATE_WRITE_PER_MIN` | `120` | every other method of one user (launching a job, sending files, editing; an upload sends 8 files per request) |

A value that is not a whole number of 1 or more is ignored with a warning in the log (the default stays). Never limited:
`GET /api/healthz`, `GET /api/workerz`, `GET /api/auth/config`, the static interface, and any request without a valid token
(it ends in 401 anyway). The counters live in the memory of each process: with several `web` instances the real limit is
multiplied by their number, and a restart resets them (at most 10 000 buckets are kept, idle ones are swept every minute).
The identity is the verified JWT, **never the IP address**, for the reason below. The interface's HTTP client
(`frontend/src/lib/api.ts`) answers a `429` by waiting `Retry-After` seconds (at most 30, 2 when absent) and sending the
same request again, up to 3 times, before showing the error: a burst such as excluding 200 images at once is spread
over time instead of failing. Only a 429 that carries `Retry-After` is replayed (the limiter always sets it; the daily
invitation limit, also a 429, does not and is final). A 429 is raised before the route code runs, so replaying a `POST`
cannot do it twice; but FastAPI reads a multipart body before it solves the dependencies, so a refused file upload has
already been received, and the interface does not replay uploads (`FormData`): they fail with the sentence, to be sent again.

**Why the rate limit is not per IP.** Evaluated and not built:
- The unauthenticated routes are static or cheap probes; the login itself never reaches Nyra.
- Behind Caddy the client IP is only known from `X-Forwarded-For`, and uvicorn (`proxy_headers=True`) trusts that header
  only from `127.0.0.1` (its `--forwarded-allow-ips` default). Caddy runs in another container, so uvicorn ignores the header and
  every visitor looks like the Caddy container: a per-IP limit would be **one bucket for the whole world**, one script would lock
  everybody out. Trusting the header blindly instead lets an attacker pick any IP and bypass the limit.
  To make it safe, `FORWARDED_ALLOW_IPS` would have to name the proxy's network and a test would have to cover a forged header.
- If probes need protecting, the firewall (DigitalOcean: inbound 22, 80, 443 only) or a rate-limit plugin in Caddy
  (not in the stock `caddy:2` image) is the right place, not the application.

### Security headers in production

The interface sends the CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` and
`Permissions-Policy`; the API sends the last four (a CSP governs documents, not JSON). Tested in
`backend/tests/test_security_headers.py` (all five headers on the interface and the four on the API, and the `default-src`, `script-src` and
`frame-ancestors` directives of the CSP). **Limit:** an unhandled exception (a 500) is answered by Starlette's outermost
error middleware, outside the header middleware, so that response carries none of them (checked with a forced error). It is JSON, so the
risk is small; not changed here. To look at the real server (read-only):

```bash
curl -sS -D - -o /dev/null https://nyra.axelproject.fr/ | grep -i -E 'content-security|x-frame|x-content-type|referrer|permissions'
```

Observed on 20 October 2026 from the agent's sandbox, with `curl -sSI https://nyra.axelproject.fr` (a HEAD, which the app answers
with 405): the CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and `Permissions-Policy` were present,
`via: 1.1 Caddy`. That was not independently reproduced: run the command above by hand and compare. There was no `Strict-Transport-Security`
header; adding `header Strict-Transport-Security "max-age=31536000"` in the Caddyfile is a one-line option, to take once
the HTTPS-only setup is certain, since browsers remember it.

### Two-factor authentication for platform administrators

Supabase Auth supports TOTP (authenticator app) as a second factor: *Authentication > Providers >
Multi-Factor* (enable TOTP), then `supabase.auth.mfa.enroll`, `challenge` and `verify` in the browser; a session that
passed the second factor carries `aal: "aal2"` in its JWT. Today Nyra has no screen for it and the backend does not read
`aal`, so enabling it in the dashboard alone protects nothing. Enforcing it for platform administrators means: an
enrollment and a code screen in the interface, and one check of the `aal` claim in `staff_dep`. A small change, but
a product decision: with no recovery path, an admin who loses the phone is locked out (keep a second admin, or a second factor enrolled). To decide by the team; nothing is implemented.

## Reviewing the members

Steps to play by hand (the code gives the list, a person decides): the first time, then every quarter and
whenever a client says someone left.

1. In Supabase (Authentication > Providers > Email), check that *Allow new users to sign up* is **off**.
   Without it anyone holding the public anon key can create an account (without access to any organization,
   but it should not exist).
2. In Nyra, Back office > Comptes: the table "administrateurs d'organisation" gives, per organization, each
   administrator's e-mail, the date they joined and their last sign-in. Ask the client to confirm the list;
   remove (« Retirer ») whoever should not be there, or switch them to « Lecture et validation ».
3. Back office > Platform admins: the list of the Nyra team; remove anyone who left.
4. In Supabase (Authentication > Users), look for accounts nobody invited (unknown e-mail, recent creation
   date) and delete them.

## Client data: activity log, export, erasure, backups

Clients trust Nyra with licensed visuals and with their decisions. Four guarantees, and what each one needs
from a person. **Only the code parts have been run (tests on a throwaway Postgres, fake Storage, fake Auth
admin). Nothing below that touches the real Supabase, the real Storage or a backup target has been played:
the procedures are to be rehearsed by the team, and the first rehearsal is also the first timing.**

### Who can do what

| Action | Who | How |
|---|---|---|
| Read the activity log of an organization | Its administrators; the Nyra team (platform admins) | Réglages > Journal d'activité; back office > Organisations > Journal d'activité; `GET /api/orgs/{org_id}/audit`, `GET /api/staff/organizations/{org_id}/audit` |
| Export an organization's data | The operator (access to the server and `.env`) | `nyra cloud-export-org` |
| Erase an organization | The operator, after the client's written request | `nyra cloud-delete-org` |
| Run the backups and the restore | The operator | the commands below |
| Brands: create and delete | Platform admins (unchanged) | interface |

The operator is whoever can run `docker compose run --rm web nyra …` on the server: today, the Nyra team.
No route of the API exports or erases a whole organization; a client asks, the operator acts.

### Activity log

Table `audit_log` (migration `20261020000031`): one row per action of a person that changes something, with the
organization, the brand, the author (account id; the e-mail is read from Auth when the log is shown), a stable
action code (`library.delete`, `exclusion.add`, `review.set`, `member.remove`, `job.crawl`…), the object
(type and identifier) and a small `detail` of counters and codes. **No content**: no request body, no file name,
no address, no reason typed by a person. `audit.record` refuses anything but numbers, flags and short codes.

- Written by the route itself, in the same database transaction as the change. If the row cannot be written, the
  change is rolled back and the person gets an error (HTTP 500, « Erreur interne du serveur »): an action that
  leaves no trace does not happen. One exception by construction: an upload or an adoption writes one row per
  file, with that file's reference; if the row fails that file is reported as refused to the person (the file
  already sent to Storage stays as a harmless orphan). Decision recorded in `backend/nyra/cloud/audit.py`.
- Covered: library (upload, edit, delete, expiry, tags, CSV import, adoption of site images, collections),
  decisions (`review.set`, `visual.replaced`), exclusions, settings, logos, labels, team (invite, role,
  removal), jobs started or cancelled by a person, brands, addresses, organization creation. Not covered, on
  purpose: reads; jobs the system queues by itself after a change (the change carries the cause); the platform
  admin list is traced too, with no organization (`org_id` null, `staff.add` / `staff.remove`; readable in SQL
  only). `test_audit.py` lists every route that changes something and fails for one without an audit call on the
  connection of its action (read from the syntax tree), so a new route cannot forget it.
- **Retention: 12 months.** `audit.purge` deletes older rows in batches of 1,000 (one transaction each). The
  worker runs it once a day; by hand: `nyra cloud-purge-audit [--months 12]`.
- The platform admin who acts as an administrator of a client organization appears under their own e-mail.
- An export writes an `organization.export` row first; an erasure removes the log with the organization (the
  trace of the erasure itself is below).

### Exporting an organization

```bash
mkdir -p exports
docker compose run --rm -v "$PWD/exports:/exports" web nyra cloud-export-org client-slug --out /exports/client-slug-2026-10-20
```

Writes, one row or one file at a time (memory does not grow with the organization):

- CSV (UTF-8 with BOM, accents readable in a spreadsheet): `marques`, `sites`, `references` (brand, file, collection,
  expiry date, unlimited rights, credit, notes, tags, date added, path of the original), `decisions`, `exclusions`,
  `rapports`, `membres` (e-mail, role), `journal`;
- `files/refs/…` (the original of each reference) and `files/reports/…` (the three files of each report);
- `manifeste.json`: format and Nyra versions, date, operator, rows per CSV, files copied, failures, `complete`.

Not included, and listed in the manifest (`not_exported`): the images read on the sites, thumbnails and working copies,
labels, the collection tree, the organization's settings, job logs, brand logos. Two files whose names differ only by case
are reported as a failure (they would overwrite each other on a Mac or Windows disk). A file larger than `--max-file-mb` (200) is a failure (its size is read from Storage's listing before any download); the copy stops with an error at
`--max-total-gb` (50). A file that cannot be downloaded is listed in the manifest and in the exit code
(`complete: false`, exit 1): run the **same command again on the same folder**, files already there are kept.
Check `complete: true` before telling the client. The folder holds personal data (e-mails) and licensed images:
encrypt it (`age`, `gpg`) before it leaves the server, hand it over through a channel agreed with the client, and
delete the local copy.

### Erasing an organization

```bash
docker compose run --rm web nyra cloud-delete-org client-slug --dry-run     # lists, deletes nothing
docker compose run --rm web nyra cloud-delete-org client-slug               # asks to type the exact name
```

1. Export first if the client wants its data, and keep the written request.
2. `--dry-run` prints the report: rows per table, files per bucket, the accounts that would go. Read it.
3. The real run takes the organization's **exact slug** (not its normalized form: `ACME!!` does not find `acme`) and
   its **exact name** (typed at the prompt, or `--confirm-name`). `--operator` is a short identifier
   (`[A-Za-z0-9._-]`, 40 characters), never an e-mail address: it is kept in the trace.
4. It works in four steps, each able to resume after a failure:
   1. one short transaction **closes** the organization: row locked, refused if a task is queued or running, the
      trace written once (`deleted_organizations`, with the ids of the accounts to delete), and **every
      membership removed**, so nobody can upload or start anything any more (an upload is answered 403);
   2. the Storage files under `{org_id}/` in `refs`, `site-images` and `reports` (batches of 100, checked empty);
   3. the organization row, every other table following through `on delete cascade`, then a check that no row of the
      organization is left (otherwise this step is rolled back); a last sweep of the files for a request that was
      already under way at step 1;
   4. the Auth accounts noted at step 1, **checked again just before**: one that has joined another organization
      or the Nyra team in the meantime is kept.
5. After a failure at any step the message says so and prints what was done: **run the same command again**. A
   closed organization (alive, no member) or one already gone (only its trace left) is recognised and accepted, the
   trace is not duplicated. The report is printed at the end; an account that could not be deleted exits 1 (the
   next run retries it, or delete it in Supabase > Authentication).
6. **Trace**: a log line `ORGANIZATION DELETED {json}` (identifier, name, slug, operator, counts; no e-mail) and
   one row in `deleted_organizations` (identifier, name, slug, date, operator, counts, ids of the accounts). Its
   **retention is to be decided by the team** (nothing purges it today). The organization's own activity log goes
   with it. Keep the logs long enough to answer an audit and give the client the date and the report.

A request that was already under way when the organization was closed could still send a file after the last
sweep: if you must be certain, check `rclone ls`/the Storage browser for the prefix `{org_id}/` once the command
ends. That the cascade covers every table is checked on the catalog by `test_offboarding.py`: a table added
without it fails the test.

**Not erased by the command**: Supabase's daily database backups, the file copies below, the e-mails the
provider (Resend) already sent, and the logs. Tell the client the real delay: the data disappears from the
backups when they expire (**35 days at most with the setup below, plus the Supabase retention, 7 days on the
Pro plan: check it in Database > Backups**). To erase a client from the file copies sooner:
`rclone purge "scw-crypt:refs/current/<org_id>"` for each bucket, and
`rclone delete scw-crypt:refs/deleted --include "/*/<org_id>/**"`.

### Backups

What Supabase does and does not do: Postgres is backed up every day (Pro plan; point-in-time recovery is a paid
add-on). **Storage files are not backed up** (images, thumbnails, reports): losing a bucket loses them for good.
Nothing here has been run; set it up, then run the restore drill below.

**1. A copy of the three buckets, every night.** Supabase Storage has an S3 endpoint (Project settings >
Storage > S3 connection: create an access key there). On a machine that is **not** the production Droplet (a
second small VPS, or the Droplet if there is nothing else, which protects against a Supabase accident only), with
`rclone`:

```ini
# ~/.config/rclone/rclone.conf (0600)
[supabase]
type = s3
provider = Other
access_key_id = <S3 access key from Supabase>
secret_access_key = <secret>
endpoint = https://<project-ref>.storage.supabase.co/storage/v1/s3
region = <project region>

[scw]
type = s3
provider = Scaleway
access_key_id = <key limited to the backup bucket>
secret_access_key = <secret>
endpoint = s3.fr-par.scw.cloud
region = fr-par

[scw-crypt]
type = crypt
remote = scw:nyra-backups
password = <rclone obscure output>
password2 = <rclone obscure output>
```

The `crypt` remote encrypts names and contents before they leave the machine. Keep its two passwords in the
team's password manager **and** on paper in a safe place: without them the copy is unreadable, and they must not
live only on the server that is being backed up. Never write these keys in the repository.

```bash
# nightly: current state, and what was deleted or overwritten kept 35 days
for b in refs site-images reports; do
  rclone sync "supabase:$b" "scw-crypt:$b/current" --backup-dir "scw-crypt:$b/deleted/$(date +%F)" --transfers 8 --checksum --fast-list
  rclone delete "scw-crypt:$b/deleted" --min-age 35d && rclone rmdirs "scw-crypt:$b/deleted" --leave-root
done
```

Schedule with a systemd timer (`/etc/systemd/system/nyra-storage-backup.{service,timer}`): the service runs the
loop above as a dedicated user, `OnCalendar=*-*-* 03:30:00`, `Persistent=true`; `systemctl list-timers` shows
the last and next run, `journalctl -u nyra-storage-backup` the output. Failure must be seen: add
`OnFailure=` to a unit that sends an e-mail, or a Better Stack heartbeat call at the end of the script.
Rotation of the keys: yearly, and at once when someone leaves the team.

**2. A copy of the database we control**, in addition to Supabase's (it is the file the restore below reads):

```bash
pg_dump "$DATABASE_URL" --format=custom --no-owner -n public -n auth | \
  rclone rcat "scw-crypt:db/nyra-$(date +%F).dump"
rclone delete scw-crypt:db --min-age 30d
```

(`DATABASE_URL` in session mode, port 5432. `-n auth` brings `auth.users`; if Supabase refuses it for your role,
take `-t auth.users` data only, or export the users with the Dashboard. To settle at the first run.) Weekly is
enough if a day of loss is acceptable; daily costs a few hundred MB.

**3. Check, weekly and after any change:** `scripts/check-storage-backup.sh` compares number and weight of
objects in each bucket with the copy and the objects the copy lacks (exit 1 above `MAX_MISSING`). Look at the
weight of `db/` too (a dump that suddenly shrinks is a problem). Written but **not run**.

**Retention summary** (state it in the client contract): Supabase database backups 7 days (Pro, to verify);
our database dumps 30 days; deleted or overwritten files 35 days; current files as long as the organization
exists. So erased data can remain in backups up to 35 days (+ the Supabase delay).

### Restoring one organization

A bucket or table of one client was lost, or an organization was erased by mistake. Tool: a throwaway Postgres
(pgvector), the dump, the file copy. **Time every step the first time and write the figures in the table at the
end**; a restore nobody has timed is a guess.

1. **Choose the point in time** and get the dump: ours (`rclone copy scw-crypt:db/nyra-YYYY-MM-DD.dump .`) or
   Supabase's (Database > Backups > download). The newest dump taken *before* the loss.
2. **Restore it in a scratch database, never in production:**

   ```bash
   createdb nyra_restore && psql nyra_restore -c 'create extension if not exists vector; create extension if not exists pgcrypto;'
   pg_restore --no-owner --no-privileges -d nyra_restore nyra-YYYY-MM-DD.dump   # errors about Supabase-only roles: read them, most are harmless
   ```

3. **Keep only the organization** (every table follows the cascade, the same one `cloud-delete-org` relies on):

   ```sql
   delete from organizations where slug <> 'client-slug';
   delete from jobs where status in ('queued', 'running');   -- the worker must not wake up on restored work
   select count(*) from reference_images;                     -- compare with what the client expects
   ```

4. **Organization lost entirely** (erased, or the tables wiped): the accounts of its members changed if they were
   deleted (a new invitation gives a new id), so drop what points at accounts before copying:
   `delete from memberships;` and set to `null` the columns referencing `auth.users` (list them with
   `select conrelid::regclass, a.attname from pg_constraint c join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey) where c.confrelid = 'auth.users'::regclass;`).
   Then `pg_dump -Fc --data-only -n public nyra_restore | pg_restore --no-owner -d "$DATABASE_URL"` into
   production (tables empty for this organization; the dump order follows the foreign keys), then re-create the
   organization's first administrator with `nyra cloud-invite --role admin`.
   **Organization partly damaged** (a table emptied, a library deleted): copy only those tables, with
   `COPY … TO` from the scratch database and `COPY` into a staging table of production followed by
   `insert … on conflict do nothing`; table by table, parents first.
5. **Files**: for each bucket, `rclone copy "scw-crypt:refs/current/<org_id>" "supabase:refs/<org_id>"` (same for
   `site-images` and `reports`). A file removed after the dump date is in `scw-crypt:<bucket>/deleted/<date>/`.
6. **Check**: `rclone check` on the prefix; the row counts of step 3 against production; sign in as the
   client's administrator and open the library, a visual, a report; write the restore in the log
   (`insert into audit_log (org_id, action, object_type, detail) values ('<org_id>', 'organization.restore', 'organization', '{}')`).
7. Drop the scratch database (`dropdb nyra_restore`): it holds client data.

| Rehearsal | Date | Who | Dump + restore | Files | Total | Problems found |
|---|---|---|---|---|---|---|
| 1 | _not done_ | | | | | |

Rehearse once before the first client depends on it, then every quarter, on a test organization (create it, load
images, erase it, restore it). Note what you had to change in this procedure.

## Vercel (web only)

Vercel can host the **web** process; it cannot host the worker (Chromium,
torch and jobs lasting minutes don't fit serverless functions). Run the
`worker` image somewhere long-lived (Railway, Fly.io, Render, a VPS)
against the same Supabase project.

- `app.py` at the repository root is the entrypoint Vercel detects
  (`app = create_app(settings_from_env())`); `vercel.json` builds the
  interface and ships `frontend/dist`, `config.yaml` and the report
  template with the function.
- Project settings > Environment Variables: `DATABASE_URL`,
  `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
  `SUPABASE_JWT_SECRET` (if legacy), `NYRA_PUBLIC_URL`, and
  `NYRA_DB_POOL_MAX=2` so many small instances don't exhaust the
  Supabase pooler.
- The worker uses the same variables (without `NYRA_DB_POOL_MAX`).
