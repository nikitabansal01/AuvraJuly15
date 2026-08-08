FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-v2.lock .
RUN pip install \
    --no-cache-dir \
    --require-hashes \
    --prefix=/install \
    --requirement requirements-v2.lock


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    ENVIRONMENT=production

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system auvra \
    && useradd --system --gid auvra --home-dir /app --shell /usr/sbin/nologin auvra

COPY --from=builder /install /usr/local

WORKDIR /app
# The image deliberately contains no legacy routes, models, services, data,
# tests, or historical migrations.  Alembic needs only the v2 migration root
# and env.py; version_locations in alembic.ini prevents legacy migrations from
# ever being selected.
COPY --chown=auvra:auvra alembic.ini ./
COPY --chown=auvra:auvra alembic/env.py alembic/env.py
COPY --chown=auvra:auvra alembic/recovery_versions/ alembic/recovery_versions/
COPY --chown=auvra:auvra app/__init__.py app/__init__.py
COPY --chown=auvra:auvra app/v2/ app/v2/
COPY --chown=auvra:auvra contracts/ contracts/

USER auvra

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/api/v2/health/live || exit 1

# Schema migration is an explicit pre-deploy operation, never a web-process side effect.
CMD ["uvicorn", "app.v2.main:app", "--host", "0.0.0.0", "--port", "8000"]
