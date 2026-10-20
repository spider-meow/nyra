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
  images (`.dockerignore` excludes it) and screenshots; rotate it if in
  doubt.
- Public sign-up disabled in Supabase (step 2 above).
- Never set `NYRA_ALLOW_PRIVATE_HOSTS` on a server: it turns off the
  guard that keeps crawls away from private addresses and cloud metadata.
- `crawl.max_pages_limit` in `config.yaml` bounds the cost of any single
  crawl, whatever an organization asks for.

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
