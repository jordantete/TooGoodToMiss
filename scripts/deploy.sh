#!/usr/bin/env bash
# deploy.sh - sync local code to the VPS and (re)start the bot inside tmux.
#
# Expected configuration in .env (at the project root):
#   VPS_USER, VPS_HOST                (required)
#   VPS_BOT_PATH   (default /root/toogoodtomiss)
#   SSH_KEY        (default ~/.ssh/id_ed25519)
#
# WARNING - state.json is NEVER pushed to the VPS. It holds the live TGTG
# session, which the bot refreshes continuously; overwriting it with the
# stale local copy would trigger a re-login, and therefore a CAPTCHA.
# Corollary: editing ACCESS_TOKEN in .env and redeploying has NO EFFECT.
# To force new tokens:
#     ssh $VPS_USER@$VPS_HOST "rm $VPS_BOT_PATH/state.json"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "ERROR: $ENV_FILE not found. Create it from .env.example." >&2
    exit 1
fi

: "${VPS_USER:?VPS_USER is not set in .env}"
: "${VPS_HOST:?VPS_HOST is not set in .env}"
: "${VPS_BOT_PATH:=/root/toogoodtomiss}"
: "${SSH_KEY:=$HOME/.ssh/id_ed25519}"
TMUX_SESSION="toogoodtomiss"

SSH_CMD=(ssh -i "$SSH_KEY" "$VPS_USER@$VPS_HOST")

echo "=== Deploying to $VPS_HOST:$VPS_BOT_PATH ==="

"${SSH_CMD[@]}" "mkdir -p \"$VPS_BOT_PATH\""

rsync -av --delete \
    --exclude '.env' \
    --exclude 'state.json' \
    --exclude 'state.json.tmp*' \
    --exclude '.venv' \
    --exclude 'logs/' \
    --exclude '.pytest_cache' \
    --exclude '__pycache__' \
    --exclude '.git' \
    -e "ssh -i $SSH_KEY" \
    "$PROJECT_DIR/" "$VPS_USER@$VPS_HOST:$VPS_BOT_PATH/"

rsync -av -e "ssh -i $SSH_KEY" "$ENV_FILE" "$VPS_USER@$VPS_HOST:$VPS_BOT_PATH/.env"
"${SSH_CMD[@]}" "chmod 600 \"$VPS_BOT_PATH/.env\""

"${SSH_CMD[@]}" "cd \"$VPS_BOT_PATH\" && \
    { [ -d .venv ] || python3 -m venv .venv; } && \
    ./.venv/bin/pip install --quiet --upgrade pip && \
    ./.venv/bin/pip install --quiet -r requirements.txt"

"${SSH_CMD[@]}" "tmux kill-session -t $TMUX_SESSION 2>/dev/null || true; \
    tmux new-session -d -s $TMUX_SESSION 'cd \"$VPS_BOT_PATH\" && ./scripts/start.sh'"

echo "=== Deployment complete ==="
echo "Logs   : ${SSH_CMD[*]} 'tail -f $VPS_BOT_PATH/logs/app.log'"
echo "Session: ${SSH_CMD[*]} 'tmux attach -t $TMUX_SESSION'   (detach: Ctrl-b then d)"
