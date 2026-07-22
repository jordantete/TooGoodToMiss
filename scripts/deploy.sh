#!/usr/bin/env bash
# deploy.sh — synchronise le code local vers le VPS et (re)lance le bot dans tmux.
#
# Config attendue dans .env (a la racine du projet) :
#   VPS_USER, VPS_HOST                (obligatoires)
#   VPS_BOT_PATH   (defaut /root/toogoodtomiss)
#   SSH_KEY        (defaut ~/.ssh/id_ed25519)
#
# ATTENTION - state.json n'est JAMAIS pousse sur le VPS. Il porte la session
# TGTG vivante, que le bot rafraichit en continu ; l'ecraser par la copie
# locale perimee declencherait un re-login, donc un CAPTCHA.
# Corollaire: modifier ACCESS_TOKEN dans .env puis redeployer est SANS EFFET.
# Pour forcer de nouveaux tokens :
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
    echo "ERREUR : $ENV_FILE introuvable. Cree-le depuis .env.example." >&2
    exit 1
fi

: "${VPS_USER:?VPS_USER non defini dans .env}"
: "${VPS_HOST:?VPS_HOST non defini dans .env}"
: "${VPS_BOT_PATH:=/root/toogoodtomiss}"
: "${SSH_KEY:=$HOME/.ssh/id_ed25519}"
TMUX_SESSION="toogoodtomiss"

SSH_CMD=(ssh -i "$SSH_KEY" "$VPS_USER@$VPS_HOST")

echo "=== Deploiement vers $VPS_HOST:$VPS_BOT_PATH ==="

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

echo "=== Deploiement termine ==="
echo "Logs   : ${SSH_CMD[*]} 'tail -f $VPS_BOT_PATH/logs/app.log'"
echo "Session: ${SSH_CMD[*]} 'tmux attach -t $TMUX_SESSION'   (detacher : Ctrl-b puis d)"
