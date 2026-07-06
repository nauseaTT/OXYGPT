#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# OXYGPT — one-command Docker installer
# ─────────────────────────────────────────────────────────────────────────
# Inspired by the familiar `curl -o install.sh ... && bash install.sh` flow,
# but fully containerised: it installs Docker if missing, collects your
# credentials into a .env file, then builds and starts the bot with Compose.
#
#   Quick start (from a server with the repo cloned):
#       bash install.sh
#
#   Remote one-liner (replace <RAW_URL> with the raw install.sh URL):
#       curl -fsSL <RAW_URL> -o install.sh && bash install.sh
#
# Re-running is safe: it detects an existing .env and offers to reuse it, and
# a running stack is rebuilt in place without losing the /data volume.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── pretty output ─────────────────────────────────────────────────────────
c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_green=$'\033[32m'
c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_blue=$'\033[36m'
info()  { printf '%s[*]%s %s\n' "$c_blue"  "$c_reset" "$*"; }
ok()    { printf '%s[+]%s %s\n' "$c_green" "$c_reset" "$*"; }
warn()  { printf '%s[!]%s %s\n' "$c_yellow" "$c_reset" "$*"; }
err()   { printf '%s[x]%s %s\n' "$c_red"   "$c_reset" "$*" >&2; }
die()   { err "$*"; exit 1; }

REPO_URL="https://github.com/OxyGPT/oxygpt.git"   # adjust to your fork if needed
APP_DIR="${OXYGPT_DIR:-$(pwd)}"

banner() {
  printf '%s\n' "$c_bold$c_blue"
  cat <<'ART'
   ___  _  ____   __ ____ ____ _____
  / _ \| |/ /\ \ / /|  __| _  |_   _|
 | (_) | ' <  \ V / | |  |  __| | |
  \___/|_|\_\  |_|  |_|  |_|    |_|
        smart assistant  ·  docker deploy
ART
  printf '%s\n' "$c_reset"
}

# ── 1. ensure Docker + Compose ─────────────────────────────────────────────
ensure_docker() {
  if command -v docker >/dev/null 2>&1; then
    ok "Docker is already installed ($(docker --version))"
  else
    warn "Docker not found — installing via the official convenience script."
    if ! command -v curl >/dev/null 2>&1; then
      die "curl is required to install Docker. Please install curl and retry."
    fi
    curl -fsSL https://get.docker.com | sh \
      || die "Docker installation failed. Install Docker manually and retry."
    ok "Docker installed."
  fi

  # Compose v2 (docker compose) preferred; fall back to legacy docker-compose.
  if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
  else
    die "Docker Compose is not available. Install the Compose plugin and retry."
  fi
  ok "Using Compose command: ${COMPOSE}"

  # Make sure the daemon is actually reachable.
  if ! docker info >/dev/null 2>&1; then
    warn "Cannot talk to the Docker daemon."
    warn "Try:  sudo systemctl start docker   (and/or re-run this script with sudo)"
    die  "Docker daemon unavailable."
  fi
}

# ── 2. fetch the source if we're not already inside it ─────────────────────
ensure_source() {
  if [[ -f "${APP_DIR}/telegram.py" && -f "${APP_DIR}/Dockerfile" ]]; then
    ok "Found OXYGPT source in ${APP_DIR}"
    return
  fi
  warn "OXYGPT source not found in the current directory."
  if ! command -v git >/dev/null 2>&1; then
    die "git is required to clone the repository. Install git and retry."
  fi
  info "Cloning ${REPO_URL} ..."
  git clone "${REPO_URL}" oxygpt || die "git clone failed."
  APP_DIR="$(pwd)/oxygpt"
  ok "Cloned into ${APP_DIR}"
}

# ── 3. build the .env interactively (or reuse an existing one) ─────────────
prompt_val() {
  # prompt_val VAR "Prompt text" [default]
  local __var="$1"; local __prompt="$2"; local __default="${3:-}"
  local __input
  if [[ -n "$__default" ]]; then
    read -r -p "$(printf '%s%s%s [%s]: ' "$c_bold" "$__prompt" "$c_reset" "$__default")" __input || true
    __input="${__input:-$__default}"
  else
    read -r -p "$(printf '%s%s%s: ' "$c_bold" "$__prompt" "$c_reset")" __input || true
  fi
  printf -v "$__var" '%s' "$__input"
}

configure_env() {
  local env_file="${APP_DIR}/.env"
  if [[ -f "$env_file" ]]; then
    warn "An existing .env was found."
    read -r -p "Reuse it? [Y/n]: " reuse || true
    case "${reuse:-Y}" in
      [nN]*) info "Recreating .env ..." ;;
      *) ok "Reusing existing .env."; return ;;
    esac
  fi

  info "Let's configure your credentials. Values are written to ${env_file}."
  echo
  echo "  Get TELEGRAM_API_ID / TELEGRAM_API_HASH from https://my.telegram.org"
  echo "  Get TELEGRAM_BOT_TOKEN from @BotFather"
  echo "  Get a Gemini API key from https://aistudio.google.com/apikey"
  echo

  prompt_val API_ID    "Telegram API ID"
  prompt_val API_HASH  "Telegram API Hash"
  prompt_val BOT_TOKEN "Telegram Bot Token"
  prompt_val GEMINI_1  "Gemini API key (at least one)"
  prompt_val ADMIN_ID  "Your Telegram user id (admin, optional)" ""

  [[ -n "${API_ID}"    ]] || die "TELEGRAM_API_ID is required."
  [[ -n "${API_HASH}"  ]] || die "TELEGRAM_API_HASH is required."
  [[ -n "${BOT_TOKEN}" ]] || die "TELEGRAM_BOT_TOKEN is required."
  [[ -n "${GEMINI_1}"  ]] || die "At least one Gemini API key is required."

  umask 077  # .env holds secrets — keep it private.
  cat > "$env_file" <<EOF
# Generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
TELEGRAM_API_ID=${API_ID}
TELEGRAM_API_HASH=${API_HASH}
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}

GEMINI_API_KEY_1=${GEMINI_1}

ADMIN_USER_ID=${ADMIN_ID}
ADMIN_IDS=${ADMIN_ID}
EOF
  ok "Wrote ${env_file} (permissions 600)."
}

# ── 4. build & launch ──────────────────────────────────────────────────────
launch() {
  cd "${APP_DIR}"
  info "Building the image and starting the stack (this can take a few minutes"
  info "on the first run because Telethon v2 is compiled from source) ..."
  ${COMPOSE} up -d --build || die "Compose build/up failed."
  ok "OXYGPT is up."
  echo
  info "Follow the logs with:   ${COMPOSE} logs -f"
  info "Stop the bot with:      ${COMPOSE} stop"
  info "Update later with:      git pull && ${COMPOSE} up -d --build"
  echo
  warn "If you enabled the Channel Watcher (a user account login), watch the"
  warn "logs on first start — it may ask for the login code sent to that"
  warn "account. Set CW_USER_PHONE / CW_USER_PASSWORD in .env for headless 2FA."
}

main() {
  banner
  ensure_docker
  ensure_source
  configure_env
  launch
  ok "Done. Enjoy your smart assistant! 🚀"
}

main "$@"
