# Deployment

Nothing has actually been deployed anywhere — this documents the
artifacts (`Dockerfile`, `docker-compose.yml`) so deploying is a matter of
picking a host and pointing it at this image, whenever that's ready to
happen. The image is plain Docker, so it runs identically on a bare VPS,
Fly.io, Railway, Render, or anything else that runs an arbitrary container
— nothing here is tied to one platform.

## What's in the image

Multi-stage build (see `Dockerfile`):

1. `node:20-slim` builds `frontend/` (`npm ci && npm run build`).
2. `mcr.microsoft.com/playwright/python:v1.63.0-noble` — Python +
   Chromium + all of Playwright's system dependencies already present,
   which is why that specific base image is used instead of a plain
   `python:3.11-slim` — avoids hand-rolling Playwright's apt dependency
   list. `pip install .` installs the backend, `playwright install
   --with-deps chromium` re-confirms the browser matches whatever
   Playwright version pip actually resolved (belt and suspenders against
   the base image's pinned version and `pyproject.toml`'s `playwright>=1.44`
   ever drifting apart).

Both stages were validated for real in this environment (not just
reviewed): the backend stage was built and the resulting image was run,
confirming `pip install .` succeeds with the full dependency set
(FastAPI, Playwright, torch/open_clip, psycopg, supabase-py, pyjwt,
pgvector) and the container serves `/api/healthz` and switches into cloud
mode correctly when `DATABASE_URL` is set. The frontend stage (a plain
`npm ci && npm run build`, already exercised directly — not in Docker —
throughout this project) couldn't be pulled in this sandbox specifically
(Docker Hub access issue in this environment, unrelated to the
Dockerfile) — worth a real `docker build .` once this lands somewhere
with normal Docker Hub access, before the first real deploy.

## Resource requirements

Budget for headless Chromium (a crawl) and, unless running with
`--no-clip`/`fast` everywhere, a CLIP model resident in memory:

- **RAM**: 4 GB minimum, more if crawling large sites with CLIP enabled.
  2 GB will likely OOM under a real crawl.
- **CPU**: 2 vCPU minimum — Chromium rendering and CLIP inference both
  want real cores, not a fraction of one.
- **Disk**: local mode caches crawled images and reference images inside
  the container/volumes (`data/`, `out/`) — size to your reference
  library and crawl volume. Cloud mode keeps almost nothing on disk
  (everything is in Supabase Storage), so this matters far less there.

## Environment variables

See `.env.example` for the full list and where to find each value in the
Supabase dashboard. The short version: **presence of `DATABASE_URL` is
the mode switch** (`cli.py`'s `ui_cmd`) —

- **Unset** → local mode: SQLite + local disk, no accounts. This is the
  mode the image runs by default with an empty `.env`.
- **Set** (along with `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`,
  required alongside it) → cloud mode: Postgres + Supabase Storage +
  Supabase Auth, multi-organization. `SUPABASE_JWT_SECRET` is optional —
  see `.env.example` for when to set it.

Copy `.env.example` to `.env` and fill in what applies before running
`docker compose up`; `docker-compose.yml`'s `env_file` is optional, so an
empty/absent `.env` is fine for local mode.

## Building and running

```bash
# Build and run in one step
docker compose up -d --build

# Or without compose
docker build -t nyra .
docker run -d -p 8000:8000 --env-file .env \
  -v $(pwd)/data:/app/data -v $(pwd)/out:/app/out \
  nyra

# Health check
curl http://localhost:8000/api/healthz
# {"status": "ok"}
```

## Bootstrapping the first organization (cloud mode)

Row Level Security means nobody can create their own first organization
(see `supabase/migrations/0005_row_level_security.sql`'s comment on
`memberships` — an admin membership has to already exist to add another
one). Once a client's user has signed up through Supabase Auth:

```bash
docker compose exec backend nyra cloud-provision-org \
  --name "Client Name" --slug client-slug --admin-email admin@client.com
```

(Or run the same command directly on a host with `DATABASE_URL` set and
`nyra` installed, without going through the container at all —
see `docs/CLI_REFERENCE.md`.)

## Logs and process supervision

`docker-compose.yml` sets `restart: unless-stopped`; a platform that
manages containers itself (Fly.io, Railway, a Kubernetes deployment) will
have its own restart/health-check story layered on top of the
`/api/healthz` endpoint — point whatever health check mechanism the host
offers at it. Logs go to stdout/stderr (uvicorn's default), so `docker
logs`/the platform's log viewer is the way to read them; nothing writes
to a log file inside the container.

## Not covered here

- **CI/CD** — building and pushing the image on every push, deploying
  automatically, etc. Out of scope for now; add a workflow when there's
  an actual deploy target to push to.
- **Horizontal scaling** — the crawl/match job tracker
  (`JobRunner` in `nyra.api`/`nyra.cloud.api`) is
  in-process and per-instance (one `JobRunner` per organization in cloud
  mode, one globally in local mode). Running more than one backend
  instance behind a load balancer would give each instance its own,
  disconnected view of "is a job running" — fine for a single dedicated
  instance (what's been built and tested here), not yet for multiple.
  `crawl_runs` in Postgres (cloud mode) already persists the durable
  history regardless; only the *live* in-flight polling state is
  per-instance.
- **TLS/HTTPS** — terminate it at whatever sits in front of the
  container (the platform's own load balancer, or a reverse proxy like
  Caddy/nginx on a bare VPS) rather than in the app itself.
