#!/usr/bin/env bash
# =============================================================================
#  OXYGPT · one-line installer
# -----------------------------------------------------------------------------
#  Usage:
#     bash <(curl -fsSL https://raw.githubusercontent.com/nauseaTT/Telegram-Robatsaz/main/deploy/install.sh)
#
#  What it does:
#     1. Checks for Docker + Docker Compose (offers to install on Debian/Ubuntu)
#     2. Clones (or updates) the repo into ~/oxygpt
#     3. Walks you through creating a .env (secrets)
#     4. Builds & starts the bot with docker compose
#     5. Prints handy management commands
#
#  It is intentionally idempotent: re-running just updates & restarts.
# =============================================================================
set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/nauseaTT/Telegram-Robatsaz.git"
REPO_BRANCH="main"
INSTALL_DIR="${OXYGPT_DIR:-$HOME/oxygpt}"
APP_NAME="OXYGPT"

# ── Pretty output ────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    BOLD="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"; RESET="$(printf '\033[0m')"
    RED="$(printf '\033[31m')"; GREEN="$(printf '\033[32m')"
    YELLOW="$(printf '\033[33m')"; CYAN="$(printf '\033[36m')"
else
    BOLD=""; DIM=""; RESET=""; RED=""; GREEN=""; YELLOW=""; CYAN=""
fi

info()  { printf "%s➜%s  %s\n"  "$CYAN"  "$RESET" "$*"; }
ok()    { printf "%s✔%s  %s\n"  "$GREEN" "$RESET" "$*"; }
warn()  { printf "%s!%s  %s\n"  "$YELLOW" "$RESET" "$*"; }
die()   { printf "%s✗%s  %s\n" "$RED"   "$RESET" "$*" >&2; exit 1; }

banner() {
cat <<'EOF'
   ___  _  ____   ______ ____ _____
  / _ \| |/ /\ \ / / ___|  _ \_   _|
 | | | | ' /  \ V / |  _| |_) || |
 | |_| | . \   | || |_| |  __/ | |
  \___/|_|\_\  |_| \____|_|    |_|
        Telegram AI Bot · dockerized installer
EOF
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# ── 1. Dependency checks ─────────────────────────────────────────────────────
ensure_docker() {
    if need_cmd docker; then
        ok "Docker found: $(docker --version | cut -d, -f1)"
    else
        warn "Docker is not installed."
        if [ "$(uname -s)" = "Linux" ] && need_cmd curl; then
            read -r -p "Install Docker now via get.docker.com? [y/N] " ans
            case "$ans" in
                [yY]*)
                    info "Installing Docker…"
                    curl -fsSL https://get.docker.com | sh
                    sudo usermod -aG docker "$USER" 2>/dev/null || true
                    ok "Docker installed. You may need to log out/in for group changes."
                    ;;
                *) die "Docker is required. Install it and re-run this script." ;;
            esac
        else
            die "Please install Docker manually: https://docs.docker.com/get-docker/"
        fi
    fi

    # Compose v2 (docker compose) preferred; fall back to v1 (docker-compose).
    if docker compose version >/dev/null 2>&1; then
        COMPOSE="docker compose"
    elif need_cmd docker-compose; then
        COMPOSE="docker-compose"
    else
        die "Docker Compose not found. Install the Compose plugin and re-run."
    fi
    ok "Compose found: $($COMPOSE version | head -n1)"
}

# ── 2. Clone or update the repo ──────────────────────────────────────────────
fetch_repo() {
    if need_cmd git; then
        if [ -d "$INSTALL_DIR/.git" ]; then
            info "Updating existing install in $INSTALL_DIR …"
            git -C "$INSTALL_DIR" pull --ff-only origin "$REPO_BRANCH" || \
                warn "Could not fast-forward; keeping local version."
        else
            info "Cloning $APP_NAME into $INSTALL_DIR …"
            git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
        fi
    else
        die "git is required to fetch the project. Please install git and re-run."
    fi
    cd "$INSTALL_DIR"
    ok "Project ready at $INSTALL_DIR"
}

# ── 3. Configure secrets (.env) ──────────────────────────────────────────────
configure_env() {
    if [ -f .env ]; then
        ok ".env already exists — leaving it untouched."
        return
    fi

    [ -f .env.example ] && cp .env.example .env

    printf "\n%sLet's set the essentials.%s Press Enter to skip optional keys.\n" "$BOLD" "$RESET"

    ask() {  # ask <VAR> <prompt> <required(0/1)>
        local var="$1" prompt="$2" required="$3" val=""
        while :; do
            read -r -p "  $prompt: " val
            if [ -n "$val" ] || [ "$required" = "0" ]; then break; fi
            warn "This one is required."
        done
        # Replace or append the key in .env.
        if grep -q "^${var}=" .env 2>/dev/null; then
            sed -i.bak "s|^${var}=.*|${var}=${val}|" .env && rm -f .env.bak
        else
            printf "%s=%s\n" "$var" "$val" >> .env
        fi
    }

    ask TELEGRAM_API_ID    "Telegram API ID"        1
    ask TELEGRAM_API_HASH  "Telegram API Hash"      1
    ask TELEGRAM_BOT_TOKEN "Telegram Bot Token"     1
    ask GEMINI_API_KEY_1   "Gemini API Key (#1)"    1
    ask ADMIN_USER_ID      "Your Telegram user ID (optional)" 0

    ok "Secrets written to $INSTALL_DIR/.env"
    warn "You can edit more keys later:  nano $INSTALL_DIR/.env"
}

# ── 4. Build & run ───────────────────────────────────────────────────────────
launch() {
    mkdir -p data data/logs
    info "Building image (first run can take a few minutes)…"
    $COMPOSE build
    info "Starting $APP_NAME…"
    $COMPOSE up -d
    ok "$APP_NAME is up!"
}

# ── 5. Post-install help ─────────────────────────────────────────────────────
outro() {
cat <<EOF

${GREEN}${BOLD}All done! 🎉${RESET}  ${APP_NAME} is running in the background.

${BOLD}Handy commands${RESET} (run inside ${DIM}$INSTALL_DIR${RESET}):
  ${CYAN}$COMPOSE logs -f${RESET}        # follow live logs
  ${CYAN}$COMPOSE ps${RESET}             # container status
  ${CYAN}$COMPOSE restart${RESET}        # restart the bot
  ${CYAN}$COMPOSE down${RESET}           # stop & remove the container
  ${CYAN}$COMPOSE up -d --build${RESET}  # rebuild after code changes

All your data (sessions, DBs, logs) lives in:
  ${DIM}$INSTALL_DIR/data${RESET}

Now open Telegram and say hi to your bot. Have fun! 🤖
EOF
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    banner
    ensure_docker
    fetch_repo
    configure_env
    launch
    outro
}

main "$@"
