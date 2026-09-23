# Two images from one file:
#
#   docker build --target web -t nyra-web .        API + interface, small
#   docker build --target worker -t nyra-worker .  crawl/match/index/report jobs
#
# The web image has neither Chromium nor torch: it only validates, stores
# and queues. The worker carries both. See docs/DEPLOYMENT.md.

# --- interface ------------------------------------------------------------------
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- web -------------------------------------------------------------------------
FROM python:3.12-slim AS web
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install . && useradd --create-home --uid 10001 nyra
COPY config.yaml ./
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist
USER nyra
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/healthz')"
CMD ["nyra", "serve", "--host", "0.0.0.0", "--port", "8000"]

# --- worker ------------------------------------------------------------------------
# Playwright's image ships Chromium and its system libraries; pin the same
# Playwright version so the preinstalled browser matches the Python package.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble AS worker
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
# CPU wheels: the default PyPI torch pulls several GB of CUDA libraries.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2"
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install ".[worker]" playwright==1.63.0 && playwright install chromium
COPY config.yaml ./
# Bake the CLIP weights into the image so the first job doesn't download them.
RUN python -c "from nyra.config import load_config; from nyra.match import _load_clip; c = load_config().match; _load_clip(c.clip_model_name, c.clip_pretrained)" \
    && chmod -R a+rX /opt/hf
USER pwuser
CMD ["nyra", "worker"]
