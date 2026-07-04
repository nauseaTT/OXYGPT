#!/usr/bin/env sh
# =============================================================================
#  entrypoint.sh — wire scattered runtime state into the /app/data volume
# -----------------------------------------------------------------------------
#  The bot writes several stateful files to hard-coded relative paths. Rather
#  than mount each one individually (fragile), we mount ONE folder (/app/data)
#  and symlink every stateful path to a file *inside* that folder. That way
#  all state persists on the host, and the app code never has to change.
# =============================================================================
set -e

DATA_DIR="/app/data"
mkdir -p "$DATA_DIR" "$DATA_DIR/logs"

# link <real_path_in_app> <name_inside_data_dir>
# Ensures the parent dir exists, moves any pre-existing file into the volume
# once, then replaces the real path with a symlink into /app/data.
link() {
    real="$1"        # where the app expects the file
    target="$DATA_DIR/$2"   # where we actually keep it (persisted)

    mkdir -p "$(dirname "$real")"

    # First run: if the image shipped a real file and the volume is empty,
    # seed the volume with it so we don't lose a bundled DB.
    if [ -e "$real" ] && [ ! -L "$real" ] && [ ! -e "$target" ]; then
        mv "$real" "$target"
    fi

    # Make sure the target exists (empty file is fine for SQLite/Telethon).
    [ -e "$target" ] || : > "$target"

    # Replace the real path with a symlink into the persisted volume.
    rm -f "$real"
    ln -s "$target" "$real"
}

# ── Stateful files (edit here if the project adds more) ──
link "/app/bot.session"                    "bot.session"
link "/app/bot_database.db"                "bot_database.db"
link "/app/channel_watcher_user.session"   "channel_watcher_user.session"
link "/app/channel_watcher/channel_watcher.db" "channel_watcher.db"
link "/app/trade_journal/journal.db"       "journal.db"

# Logs: point /app/logs at the persisted folder.
rm -rf /app/logs
ln -s "$DATA_DIR/logs" /app/logs

echo "[entrypoint] state linked into $DATA_DIR — starting bot…"

# Hand off to the container's main command (CMD), PID 1 via tini.
exec "$@"
