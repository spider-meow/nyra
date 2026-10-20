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
insert into jobs (org_id, kind) select id, 'index' from organizations where slug = 'client';
```

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
  to `NYRA_IMAGE_CACHE_MB` (2048) at start (and every 200 writes). `GET /api/workerz` also answers 503 with
  `"status": "disk almost full"` when the machine's disk is 80 % full (`disk_percent` and the list `problems` are in the answer, which is public like the rest of this probe: it shows only a disk percentage), so the
  uptime monitor below alerts; the Serveur page of the back office shows the same figure. Docker's own images
  and logs are not cleaned: run `docker system prune` from time to time. A job that hits a full disk or
  memory error fails with a message that says so.
- **No worker at all**: point an uptime monitor (UptimeRobot, Better Stack...) at `GET /api/workerz`.
  It answers 503 when a queued job has waited over 5 minutes while no job runs anywhere, or the disk is
  80 % full. Keep
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
| Resend API key | **yes**, but **not in `.env`**: it is the SMTP password in Supabase | Supabase Auth | 5 |

Not secrets: `SUPABASE_URL`, `NYRA_PUBLIC_URL`, `NYRA_DOMAIN`, `NYRA_ENV`, `NYRA_RELEASE`, `NYRA_LABELS`,
`NYRA_LOG_FORMAT`, `NYRA_LOG_LEVEL`, `SENTRY_TRACES_SAMPLE_RATE`, `NYRA_DB_POOL_MAX`, `NYRA_IMAGE_CACHE`,
`NYRA_IMAGE_CACHE_MB`, `NYRA_WORKER_ALIVE_FILE`, `NYRA_ALLOW_PRIVATE_HOSTS`. Also keep the Droplet's SSH
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
   insert into jobs (org_id, kind) select id, 'index' from organizations where slug = 'test-org';
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

**Why Nyra does not add its own per-IP limit.** Evaluated and not built:
- The only unauthenticated routes are static or cheap probes; the login itself never reaches Nyra.
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
