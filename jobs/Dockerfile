# syntax=docker/dockerfile:1
# Multi-stage, non-editable install: the runtime image gets the venv and nothing else.
FROM python:3.14-slim-trixie AS builder

# Bump by hand with the uv_build pin in pyproject.toml.
COPY --from=ghcr.io/astral-sh/uv:0.11.31@sha256:ecd4de2f060c64bea0ff8ecb182ddf46ba3fcccdc8a60cfdbaf20d1a047d7437 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app/jobs

# The engine, from the `engine` build context (compose.yaml points it at the
# RaMa-Scene checkout this folder lives in). pyproject.toml's uv source is "..",
# so the engine goes in /app, one level up. Only what the package build reads.
# hadolint ignore=DL3022
COPY --from=engine pyproject.toml README.md LICENSE /app/
# hadolint ignore=DL3022
COPY --from=engine ramascene /app/ramascene

# Dependencies first: this layer stays cached until the lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable

COPY . /app/jobs
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM python:3.14-slim-trixie AS runtime

LABEL org.opencontainers.image.source="https://github.com/simonvanlierde/ramascene" \
      org.opencontainers.image.description="Thin job API around the RaMa-Scene engine: one scenario per job, one job at a time" \
      org.opencontainers.image.title="ramascene-jobs"

RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --no-create-home app

WORKDIR /app/jobs

COPY --from=builder --chown=app:app /app/jobs/.venv /app/jobs/.venv

ENV PATH="/app/jobs/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The app defaults to loopback; inside the container it has to listen on every
# interface for the published port to reach it.
ENV HOST=0.0.0.0 \
    PORT=8020

# A non-editable install leaves the engine's database behind, so it is copied
# on its own. The dataset is mounted at /data (compose.yaml). No git in the
# image: the engine commit comes in as a build argument.
ARG ENGINE_COMMIT=unknown
ARG ENGINE_BRANCH=unknown
# hadolint ignore=DL3022
COPY --from=engine db.sqlite3 /engine/db.sqlite3
ENV RAMASCENE_DB=/engine/db.sqlite3 \
    DATASETS_DIR=/data \
    DATASETS_VERSION=v4 \
    RAMASCENE_ENGINE_COMMIT=${ENGINE_COMMIT} \
    RAMASCENE_ENGINE_BRANCH=${ENGINE_BRANCH}

USER 1001:1001
EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request as r,sys; sys.exit(0 if r.urlopen('http://127.0.0.1:8020/health').status==200 else 1)"]

CMD ["python", "-m", "ramascene_jobs"]
