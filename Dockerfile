# syntax=docker/dockerfile:1.7
# =============================================================================
#  OXYGPT — Telegram AI Bot  ·  Production Dockerfile (multi-stage)
# -----------------------------------------------------------------------------
#  Stage 1 "builder": compiles wheels into an isolated virtualenv.
#  Stage 2 "runtime":  copies only that venv onto a slim base — no compilers,
#                      no build headers, no pip cache → smaller & safer image.
# =============================================================================

# ─────────────────────────── Stage 1: builder ───────────────────────────────
FROM python:3.12-slim AS builder

# Faster, quieter, deterministic Python & pip behaviour during build.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build-time system deps needed to compile some wheels (pandas/matplotlib/pillow).
# These live ONLY in the builder stage and never reach the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libjpeg-dev \
        zlib1g-dev \
        libfreetype6-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Create a self-contained virtualenv we will copy wholesale into runtime.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy ONLY requirements first → this layer is cached until deps change,
# so day-to-day code edits don't trigger a full re-install (fast rebuilds).
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ─────────────────────────── Stage 2: runtime ───────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="OXYGPT Telegram Bot" \
      org.opencontainers.image.description="Multi-provider AI Telegram bot (Telethon + Gemini/OpenAI)" \
      org.opencontainers.image.authors="nauseaTT" \
      org.opencontainers.image.source="https://github.com/nauseaTT/Telegram-Robatsaz"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=UTC

# RUNTIME-ONLY system libraries (shared objects the wheels link against).
# No compilers here — much smaller attack surface than the builder stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        libfreetype6 \
        libpng16-16 \
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the ready-made virtualenv from the builder — no pip install at runtime.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# ── Run as a non-root user (security best practice) ──
# The bot writes sessions/DBs/logs, so we own /app to that user.
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . .

# Directories that hold runtime state; mounted as volumes in compose.
RUN mkdir -p /app/logs /app/data && chown -R appuser:appuser /app/logs /app/data

USER appuser

# ── Lightweight healthcheck ──
# The bot has no HTTP port, so we just prove the Python process/deps import OK.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import telethon, google.generativeai" || exit 1

# entrypoint.sh symlinks scattered state into the /app/data volume before start.
# tini = tiny init: reaps zombies & forwards signals so Ctrl-C / docker stop
# shut the bot down cleanly instead of leaving orphaned asyncio tasks.
ENTRYPOINT ["tini", "--", "/app/deploy/entrypoint.sh"]

# The bot is a single long-running asyncio process.
CMD ["python", "telegram.py"]
