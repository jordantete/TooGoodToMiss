#!/usr/bin/env bash
# start.sh - run the bot in the foreground (invoked by the tmux session in deploy.sh).

set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs

if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

echo "Starting toogoodtomiss at $(date -u +%Y-%m-%dT%H:%M:%SZ) ..." >> logs/app.log
exec ./.venv/bin/python -m app.main >> logs/app.log 2>&1
