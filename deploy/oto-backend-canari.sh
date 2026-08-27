#!/bin/bash
# Source versionnée : oto-backend/deploy/oto-backend-canari.sh (repo otomata-tech/oto-backend)
#   — modifier là puis recopier ici (/opt/deploy/oto-backend-canari.sh sur oto-platform).
# 27/08/2026 : sonde directe (la sonde via mcp.oto.ninja interrogeait le canari depuis le 06/07).
# Deploiement build-serveur d'oto-backend PREPROD (oto-mcp-canari), execute par le
# runner self-hosted via `sudo /opt/deploy/oto-backend-canari.sh` (sudoers NOPASSWD
# scope). Modele de release : push sur `main` -> PREPROD (CE script) ; TAG vX.Y.Z ->
# prod (oto-backend.sh). L'environnement (checkout /opt/oto-mcp-canari, service
# oto-mcp-canari, endpoint mcp-canari.oto.ninja, base oto_canari) reste le staging de
# l'ADR 0040 ; seule la branche source devient `main`. Source de verite : CE fichier.
set -uo pipefail

cd /opt/oto-mcp-canari || exit 1
HEALTH="http://127.0.0.1:9105/.well-known/oauth-authorization-server"   # canari EN DIRECT (mcp-canari.oto.ninja) — pas via Caddy
PREV=$(git rev-parse HEAD)            # commit courant, pour rollback

# reset -> reinstall -> restart, chaines : non-zero si une etape echoue.
# pip NE reinstalle PAS une dep VCS deja presente -> on force-reinstalle oto-core
# depuis le tag LU du pyproject (source unique, pas de hardcode).
deploy() {
  git reset --hard "$1" || return 1
  tag=$(grep -oP 'oto-core\.git@\K[^"]+' pyproject.toml) || return 1
  ./.venv/bin/pip install -e . --quiet \
    && ./.venv/bin/pip install --force-reinstall --quiet "oto-core[browser] @ git+https://github.com/otomata-tech/oto-core.git@${tag}" \
    && systemctl restart oto-mcp-canari
}
# service actif ET l'app repond 200 en direct sur son port (retry ~120s : boot domine par les mounts).
healthy() {
  for i in $(seq 1 40); do
    systemctl is-active --quiet oto-mcp-canari && curl -fsS --max-time 10 "$HEALTH" -o /dev/null 2>/dev/null && return 0
    sleep 3
  done
  return 1
}

git fetch origin main || exit 1
if deploy origin/main && healthy; then
  echo "deploy preprod OK"; journalctl -u oto-mcp-canari -n 10 --no-pager
else
  echo "DEPLOIEMENT PREPROD KO (install/restart/healthcheck) -> rollback vers $PREV"
  journalctl -u oto-mcp-canari -n 50 --no-pager
  deploy "$PREV" && healthy && echo "rollback OK (restaure $PREV)" || echo "ROLLBACK EN ECHEC — intervention manuelle requise"
  exit 1
fi
