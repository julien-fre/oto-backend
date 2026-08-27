#!/bin/bash
# Source versionnée : oto-backend/deploy/oto-backend.sh (repo otomata-tech/oto-backend)
#   — modifier là puis recopier ici (/opt/deploy/oto-backend.sh sur oto-platform).
# 27/08/2026 : sonde directe (la sonde via mcp.oto.ninja interrogeait le canari depuis le 06/07).
# Deploiement build-serveur d'oto-backend (oto-mcp) PROD, execute par le runner
# self-hosted via `sudo /opt/deploy/oto-backend.sh <tag>` (sudoers NOPASSWD scope).
# Modele de release : push sur `main` -> PREPROD (oto-backend-canari.sh) ; TAG vX.Y.Z
# -> PROD (CE script). Le tag a deployer est passe en argument par le workflow
# (github.ref_name) ; pas de defaut (prod = acte explicite, aucun fallback branche).
# Source de verite du deploiement prod : CE fichier. Cf. ADR 0020.
set -uo pipefail

REF="${1:-}"
[ -n "$REF" ] || { echo "usage: oto-backend.sh <tag>  (ex: v1.2.3)" >&2; exit 2; }

cd /opt/oto-mcp || exit 1
HEALTH="http://127.0.0.1:9103/.well-known/oauth-authorization-server"   # prod EN DIRECT (mcp.oto.cx) — pas via Caddy
PREV=$(git rev-parse HEAD)            # commit courant, pour rollback

# reset -> reinstall -> restart, chaines : non-zero si une etape echoue.
# pip NE reinstalle PAS une dep VCS deja presente -> on force-reinstalle oto-core
# depuis le tag LU du pyproject (source unique, pas de hardcode).
deploy() {
  git reset --hard "$1" || return 1
  tag=$(grep -oP 'oto-core\.git@\K[^"]+' pyproject.toml) || return 1
  ./.venv/bin/pip install -e . --quiet \
    && ./.venv/bin/pip install --force-reinstall --quiet "oto-core[browser] @ git+https://github.com/otomata-tech/oto-core.git@${tag}" \
    && systemctl restart oto-mcp
}
# service actif ET l'app repond 200 en direct sur son port (retry ~120s : boot domine par les mounts).
healthy() {
  for i in $(seq 1 40); do
    systemctl is-active --quiet oto-mcp && curl -fsS --max-time 10 "$HEALTH" -o /dev/null 2>/dev/null && return 0
    sleep 3
  done
  return 1
}

git fetch --tags --force origin || exit 1
if deploy "$REF" && healthy; then
  echo "deploy prod OK ($REF)"; journalctl -u oto-mcp -n 10 --no-pager
else
  echo "DEPLOIEMENT PROD KO ($REF) -> rollback vers $PREV"
  journalctl -u oto-mcp -n 50 --no-pager
  deploy "$PREV" && healthy && echo "rollback OK (restaure $PREV)" || echo "ROLLBACK EN ECHEC — intervention manuelle requise"
  exit 1
fi
