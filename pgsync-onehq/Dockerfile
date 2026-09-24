# syntax=docker/dockerfile:1

# =============================================================================
# Builder - build and install PGSync from repository source
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /src

COPY pyproject.toml setup.py setup.cfg MANIFEST.in README.rst LICENSE ./
COPY pgsync/ ./pgsync/
COPY requirements/ ./requirements/
COPY bin/ ./bin/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install --no-cache-dir .

# =============================================================================
# Runtime - PGSync OneHQ CORE runtime
# =============================================================================
FROM python:3.12-slim

LABEL org.opencontainers.image.title="PGSync CORE" \
      org.opencontainers.image.description="Shared PGSync runtime built from repository source"

USER 0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy only the already-built Python environment
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Shared runtime components
COPY pgsync-onehq/entrypoint.sh /app/entrypoint.sh
COPY pgsync-onehq/scripts/ /app/scripts/

RUN chmod 0555 /app/entrypoint.sh \
    && find /app/scripts -type f -name '*.py' -exec chmod 0555 {} + \
    && find /app -type d -exec chmod 0755 {} + \
    && find /app -type f \
         ! -path '/app/entrypoint.sh' \
         ! -path '/app/scripts/*' \
         -exec chmod 0444 {} + \
    && chgrp -R 0 /app /opt/venv \
    && chmod -R g+rX /app /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED="1" \
    PYTHONDONTWRITEBYTECODE="1" \
    SCHEMA_PATH="/app/schema.json" \
    RESOLVED_SCHEMA="/tmp/schema.resolved.json"

USER 1001

ENTRYPOINT ["/app/entrypoint.sh"]
