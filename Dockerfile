# Multi-stage: build the frontend with Node, run the backend with Python +
# Playwright's Chromium (already present in the base image below, which
# saves reinventing Playwright's apt dependency list here).
#
# Works the same on any host that runs an arbitrary Docker image (Fly.io,
# Railway, Render, a plain VPS via docker compose) — see docs/DEPLOYMENT.md
# for what each needs and how much RAM/CPU to give the container.

FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Pin the exact Playwright Python version so pip and the base image's
# preinstalled browser can never drift apart; playwright install below is
# a second safety net in case pyproject.toml's own constraint ever
# resolves to something newer than this image ships.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble AS backend

WORKDIR /app

COPY pyproject.toml requirements.txt README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir . && \
    playwright install --with-deps chromium

COPY config.yaml ./
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Local (no DATABASE_URL) mode persists here — irrelevant in cloud mode,
# where everything lives in Postgres/Supabase Storage instead.
RUN mkdir -p /app/data /app/out

EXPOSE 8000

# nyra ui picks local vs. cloud mode from the environment (see
# cli.py's ui_cmd) — nothing here needs to know which one is active.
CMD ["nyra", "ui", "--host", "0.0.0.0", "--port", "8000"]
