#!/usr/bin/env bash
# start.sh — lance le bot en avant-plan (appele par la session tmux dans deploy.sh).

set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs

if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

echo "Demarrage toogoodtomiss a $(date -u +%Y-%m-%dT%H:%M:%SZ) ..." >> logs/app.log
exec ./.venv/bin/python -m app.main >> logs/app.log 2>&1
