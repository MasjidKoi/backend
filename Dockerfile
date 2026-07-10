# Stage 1: Build — install dependencies with uv
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_HTTP_TIMEOUT=300

WORKDIR /app

# Install dependencies first (cached layer)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy source and install the project itself
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 1b: Migrate — builder + dev deps (psycopg2 sync driver) so Alembic can
# run. The runtime image deliberately ships neither the dev deps nor the
# migrations/ + alembic.ini tree, so prod migrations run from this stage instead.
# Build with:  docker compose -f docker-compose.prod.yml run --rm migrate
FROM builder AS migrate
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen
CMD ["uv", "run", "alembic", "upgrade", "head"]

# Stage 2: Runtime — lean image, no uv, no build tools
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# weasyprint native deps (PRD 05 donation receipts) — Pango/Cairo + base fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/app /app/app

USER appuser

EXPOSE 8000

# --proxy-headers + --forwarded-allow-ips=* so request.client.host reflects the
# real client from Caddy's X-Forwarded-For, not Caddy's container IP — otherwise
# every external client collapses into one rate-limit bucket and the logs record
# the proxy (CODEBASE_AUDIT #9). Trusting all upstreams is safe here: the api is
# only `expose`d on the internal docker network and is unreachable except via
# Caddy (docker-compose.prod.yml), so no untrusted peer can spoof the header.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
