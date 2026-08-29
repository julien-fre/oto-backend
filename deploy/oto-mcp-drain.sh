#!/bin/bash
# ============================================================================
# Vidange DIFFÉRÉE d'une couleur bleu/vert du backend oto.
#
# Source versionnée : otomata-tech/oto-backend, deploy/oto-mcp-drain.sh
#   — modifier là, puis recopier dans /opt/deploy/ sur oto-platform.
#
# POURQUOI SÉPARÉ DU DÉPLOIEMENT. On déploie >100 fois par mois : un déploiement
# qui reste ouvert le temps de la vidange bloquerait le suivant (verrou de
# concurrence) pour rien. Le déploiement s'arrête donc dès que la bascule d'amont
# est validée — c'est ça, le déploiement — et l'extinction de l'ancienne couleur
# devient CE travail différé, lancé en unité transitoire (systemd-run) et journalisé.
# Coût de la coexistence : ~230 Mo par instance, à comparer aux Go libres de la box.
#
# CE QUE LA VIDANGE PROTÈGE, ET CE QU'ELLE NE PROTÈGE PAS (mesuré le 28/08/2026) :
#   ✅ les requêtes EN VOL sur l'ancienne couleur — Caddy laisse finir ce qui est
#      commencé (grâce « éternelle »), et tant que la requête dure la connexion
#      reste établie, donc on ne coupe pas ;
#   ❌ l'IDENTITÉ de session MCP. Le registre de sessions est en mémoire PAR
#      instance, et au `reload` Caddy ferme les connexions inactives : le client se
#      reconnecte, atterrit sur la NOUVELLE couleur, qui ne connaît pas sa session
#      → 404 « Session not found ». Garder l'ancienne couleur en vie n'y change rien
#      tant que rien ne ROUTE les anciennes sessions vers elle. Il faudrait de
#      l'affinité par en-tête Mcp-Session-Id, ou un registre de sessions partagé.
#      Conséquence pratique : le compteur de connexions retombe à zéro en quelques
#      secondes, donc le plafond ci-dessous ne mord presque jamais aujourd'hui.
#
# usage: oto-mcp-drain.sh <gabarit-unité> <couleur> <port> <plafond_s>
# ============================================================================
set -uo pipefail

UNIT="${1:?gabarit systemd attendu}"
COLOR="${2:?couleur attendue}"
PORT="${3:?port attendu}"
MAX="${4:-3600}"

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

t0=$SECONDS
n=$(ss -Htn state established "( sport = :$PORT )" 2>/dev/null | wc -l)
peak=$n
log "vidange de ${UNIT}@${COLOR} (:${PORT}) — ${n} connexion(s) établie(s), plafond ${MAX}s"

while [ "$n" -gt 0 ]; do
  if [ $((SECONDS - t0)) -ge "$MAX" ]; then
    log "plafond ${MAX}s atteint, ${n} connexion(s) encore établie(s) — on arrête quand même"
    break
  fi
  sleep 2
  n=$(ss -Htn state established "( sport = :$PORT )" 2>/dev/null | wc -l)
  [ "$n" -gt "$peak" ] && peak=$n
  # Décompte au fil de l'eau : c'est cette trace qui permettra de régler le plafond
  # sur la durée RÉELLE des sessions plutôt qu'au jugé.
  [ $(( (SECONDS - t0) % 10 )) -eq 0 ] && log "${n} connexion(s) à +$((SECONDS - t0))s"
done

log "plus de connexion après $((SECONDS - t0))s (max vu ${peak}) — arrêt de ${UNIT}@${COLOR}"
systemctl stop "${UNIT}@${COLOR}"
# Transition depuis l'unité SIMPLE d'avant le bleu/vert : tant qu'elle n'a pas été
# retirée, c'est ELLE qui occupe le port de la couleur bleue. No-op une fois arrêtée.
systemctl stop "${UNIT}" 2>/dev/null
log "vidange terminée"
