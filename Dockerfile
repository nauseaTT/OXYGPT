# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────────────────────────────────
# OXYGPT — production container image
# ─────────────────────────────────────────────────────────────────────────
# Multi-stage build:
#   1. `builder`  installs all Python dependencies into a virtualenv, taking
#      care of the Telethon v2 codegen build quirk (see below).
#   2. `runtime`  copies just the venv + app, runs as a non-root user, and
#      keeps all persistent state on a mounted volume (OXYGPT_DATA_DIR).
#
# Why a custom build step for Telethon v2:
#   Telethon v2 (2.0.0a0) is installed from its `v2` git branch and generates
#   its Telegram TL types at *build* time. That codegen imports
#   `typing_extensions`. pip builds wheels in an ISOLATED environment that
#   does NOT include `typing_extensions`, so a naive `pip install` crashes with
#   `ModuleNotFoundError: No module named 'typing_extensions'`.
#   The fix is to pre-install the build prerequisites and pass
#   `--no-build-isolation`, which is exactly what the builder stage does.
# ─────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS builder

# git is needed to install Telethon v2 from its git branch; build-essential
# covers any C extensions (e.g. wheels that fall back to source builds).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build everything into an isolated virtualenv we can copy wholesale later.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt ./

# Step 1: build prerequisites for Telethon v2's codegen (see header note).
RUN pip install "typing_extensions>=4.12" setuptools wheel

# Step 2: install Telethon v2 WITHOUT build isolation so the codegen can see
# the typing_extensions we just installed. It is the first requirement line.
RUN pip install --no-build-isolation \
        "telethon @ git+https://github.com/LonamiWebs/Telethon.git@v2#subdirectory=client"

# Step 3: install the remaining runtime dependencies. Telethon is already
# satisfied, so this resolves the rest of requirements.txt normally.
RUN pip install -r requirements.txt


# ─────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime needs no compilers; keep the image small. `tini` gives us proper
# PID-1 signal handling so Ctrl-C / `docker stop` shut the bot down cleanly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# Bring in the pre-built virtualenv from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # All persistent state (session files + SQLite databases) lives here so a
    # single mounted volume survives image rebuilds.
    OXYGPT_DATA_DIR=/data

WORKDIR /app

# Copy the application source. Tests, git metadata, and local state are
# excluded via .dockerignore to keep the image lean and reproducible.
COPY . /app

# Run as an unprivileged user and give it ownership of the state volume.
RUN useradd --create-home --uid 10001 oxygpt \
    && mkdir -p /data \
    && chown -R oxygpt:oxygpt /app /data
USER oxygpt

VOLUME ["/data"]

# A lightweight liveness probe: the process must be able to import its own
# modules (catches a broken image before it silently restart-loops).
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import telethon_compat, database, paths" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "telegram.py"]
