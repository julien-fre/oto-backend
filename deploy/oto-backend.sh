#!/bin/bash
# Source versionnée : oto-backend/deploy/oto-backend.sh (repo otomata-tech/oto-backend)
#   — modifier là puis recopier ici (/opt/deploy/oto-backend.sh sur oto-platform).
# Déploiement build-serveur d'oto-backend PROD (oto-mcp), exécuté par le runner
# self-hosted via `sudo /opt/deploy/oto-backend.sh <tag>` (sudoers NOPASSWD scope).
# Modèle de release : push sur `main` -> PRÉPROD (oto-backend-canari.sh) ; TAG
# vX.Y.Z -> PROD (CE script). Le tag est passé en argument par le workflow
# (github.ref_name) ; pas de défaut (prod = acte explicite, aucun fallback branche).
# Cf. ADR 0020. Endpoints : mcp.oto.cx, les hôtes des tenants tiers, *.mcp.oto.cx, *.share.oto.cx.
#
# 28/08/2026 — BLEU/VERT. Avant : `systemctl restart` sec, 36-39 s port fermé, donc
# TOUTE session MCP ouverte coupée à chaque release. Maintenant : la nouvelle
# version démarre à côté sur le port de la couleur inactive, on la valide en direct,
# on bascule l'amont Caddy (reload gracieux — les connexions établies CONTINUENT sur
# l'ancienne couleur), on DRAINE l'ancienne (jusqu'à 10 min, le temps que les agents
# finissent leurs sessions), puis on l'arrête. Détail : deploy/oto-mcp-bluegreen.sh.
# ⚠️ L'amont de l'on-demand TLS (`ask` -> /api/mcp/tls-check) bascule AVEC le reste :
# il est dans le même fichier d'amont généré, sinon les certificats à la demande
# casseraient dès l'arrêt de l'ancienne couleur.
#
# Usage : oto-backend.sh v1.2.3      -> déploie ce tag
#         oto-backend.sh --rollback  -> rebascule sur la couleur inactive, qui
#                                       porte encore la version précédente.
set -uo pipefail

REF="${1:-}"
[ -n "$REF" ] || { echo "usage: oto-backend.sh <tag>  (ex: v1.2.3) | --rollback" >&2; exit 2; }

BG_ENV=prod
BG_UNIT=oto-mcp
BG_TREE=/opt/oto-mcp
BG_PORT_blue=9103
BG_PORT_green=9107
BG_UPSTREAM=/etc/caddy/upstream-oto-prod.conf
BG_SNIPPET=oto_prod
BG_DOCSHARE_HOST=mcp.oto.cx
BG_ASK=1                     # la prod porte le `ask` de l'on-demand TLS
BG_ACTIVE=/etc/oto-mcp/active-prod
BG_PUBLIC="https://mcp.oto.cx/.well-known/oauth-authorization-server"
# Plafond de la vidange. Mesuré le 28/08 sous charge réelle : la vidange ne protège
# PAS les sessions MCP (elles tombent en <4 s, le client rouvre), seulement les
# requêtes déjà en cours. Donc pas de raison de faire vivre deux versions longtemps —
# et une bonne raison de ne pas le faire : deux versions écrivent dans la MÊME base.
BG_DRAIN_MAX=120

. /opt/deploy/oto-mcp-bluegreen.sh

# Timer de maintenance (ADR 0065 lot 0, oto-backend#533) : il vit au niveau de
# l'HÔTE, pas de la couleur — donc UNE fois par déploiement, APRÈS la bascule, et
# jamais dans le démarrage d'une couleur (sinon les deux couleurs se disputeraient
# les mêmes lignes). On lit les unités dans l'arbre de la couleur qui vient d'être
# activée : le chemin figé /opt/oto-mcp de #533 lirait la couleur PRÉCÉDENTE.
# Ne fait JAMAIS échouer le déploiement — une maintenance manquée se rattrape,
# un service arrêté non.
install_timers() {
  local tree="$1" u
  for u in oto-mcp-maintenance.service oto-mcp-maintenance.timer; do
    if [ ! -f "$tree/deploy/$u" ]; then
      echo "maintenance : $u absent de $tree (tag antérieur au lot 0) — ignoré"
      return 0
    fi
    install -m 0644 "$tree/deploy/$u" "/etc/systemd/system/$u" || return 0
  done
  systemctl daemon-reload || return 0
  systemctl enable --now oto-mcp-maintenance.timer || return 0
  echo "maintenance : timer actif, prochain tir $(systemctl show -p NextElapseUSecRealtime --value oto-mcp-maintenance.timer)"
}

if bg_run "$REF"; then
  install_timers "$(bg_tree "$(bg_active)")"
else
  exit 1
fi
