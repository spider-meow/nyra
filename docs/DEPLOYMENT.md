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
processes; `web` 150 MiB; `caddy` and `autoheal` under 15 MiB. About 2 GiB for the containers, before the system.
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

- **web**: 256–512 MB, a fraction of a CPU.
- **worker**: 2 GB RAM was the estimate; measured at 1.9 GiB in use during a crawl with CLIP, so plan on 4 GB
  for the machine (see the measure above), 2 vCPU. `shm_size: 1gb`
  for Chromium. Crawl speed scales with `crawl.concurrency` (3 pages at a
  time by default) until CPU saturates.

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
- **A hung worker**: the worker touches a file every few seconds and the `worker` image's
  `HEALTHCHECK` reads its age. A platform that restarts unhealthy containers (Fly, Railway, Render,
  Kubernetes) restarts it. Plain `docker compose` only reports `unhealthy`: restart it from
  systemd or a small watcher. Whatever runs the worker, a job running over 6 hours (`JOB_MAX_SECONDS`
  in `cloud/worker.py`) makes it exit, so the restart policy above takes over and the job is retried.
- **No worker at all**: point an uptime monitor (UptimeRobot, Better Stack...) at `GET /api/workerz`.
  It answers 503 when a queued job has waited over 5 minutes while no job runs anywhere. Keep
  `GET /api/healthz` for the web process. The interface already tells an administrator that a task
  waits for a worker after a minute.

## Security checklist

- `.env` holds the service-role key: keep it out of synced folders,
  images (`.dockerignore` excludes it) and screenshots; rotate it if in
  doubt.
- Public sign-up disabled in Supabase (step 2 above).
- Never set `NYRA_ALLOW_PRIVATE_HOSTS` on a server: it turns off the
  guard that keeps crawls away from private addresses and cloud metadata.
- `crawl.max_pages_limit` in `config.yaml` bounds the cost of any single
  crawl, whatever an organization asks for.

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
  leaves no trace does not happen. Decision recorded in `backend/nyra/cloud/audit.py`.
- Covered: library (upload, edit, delete, expiry, tags, CSV import, adoption of site images, collections),
  decisions (`review.set`, `visual.replaced`), exclusions, settings, logos, labels, team (invite, role,
  removal), jobs started or cancelled by a person, brands, addresses, organization creation. Not covered, on
  purpose: reads; jobs the system queues by itself after a change (the change carries the cause); the platform
  admin list (no organization: logged as `platform admin … added/removed by …`). `test_audit.py` lists every
  route that changes something and fails for one without an audit call, so a new route cannot forget it.
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

Not included: the images read on the sites (they can be read again from the sites) and the thumbnails and
working copies. A file larger than `--max-file-mb` (200) is a failure; the copy stops with an error at
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
3. The real run refuses without the organization's **exact name** (typed at the prompt, or `--confirm-name`),
   and while a task is queued or running (cancel it, wait).
4. It deletes, in this order: the Storage files under `{org_id}/` in `refs`, `site-images` and `reports`
   (listed and removed by batches of 100, checked empty at the end); the organization row, every other table
   following through `on delete cascade` in the same transaction as the trace, then checked to hold no row of
   the organization (otherwise the whole thing is rolled back); then the Auth accounts that were members of
   this organization only. A member of another organization or of the Nyra team is never deleted.
5. It prints the report (same shape as the dry run). If an account could not be deleted it says so and exits 1:
   delete it in Supabase > Authentication.
6. **Trace**: a log line `ORGANIZATION DELETED {json}` (identifier, name, slug, operator, counts; no e-mail)
   and one row in `deleted_organizations` (identifier, name, slug, date, operator, counts). The organization's
   own activity log goes with it. Keep the logs long enough to answer an audit (set the retention of your log
   host accordingly) and give the client the date and the report.

Run it while the client is not using the product: a file uploaded between the Storage step and the database step
would stay behind (run the command again with the same slug only if the organization row still exists; otherwise
delete the leftover prefix by hand). That the cascade covers every table is checked on the catalog by
`test_offboarding.py`: a table added without it fails the test.

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
