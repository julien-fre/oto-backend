#!/bin/bash
# Source versionnée : oto-backend/deploy/oto-backend-canari.sh (repo otomata-tech/oto-backend)
#   — modifier là puis recopier ici (/opt/deploy/oto-backend-canari.sh sur oto-platform).
# Déploiement build-serveur d'oto-backend PRÉPROD (canari), exécuté par le runner
# self-hosted via `sudo /opt/deploy/oto-backend-canari.sh` (sudoers NOPASSWD scope).
# Modèle de release : push sur `main` -> PRÉPROD (CE script) ; TAG vX.Y.Z -> prod
# (oto-backend.sh). Endpoints : mcp.oto.ninja, mcp-canari.oto.ninja, *.mcp.oto.ninja.
#
# 28/08/2026 — BLEU/VERT. Avant : `systemctl restart` sec, 36-39 s port fermé à
# chaque déploiement (>100/mois), donc sessions MCP coupées. Maintenant : la
# nouvelle version démarre à côté sur le port de la couleur inactive, on la valide
# en direct, on bascule l'amont Caddy (reload gracieux), on draine l'ancienne
# couleur, puis on l'arrête. Détail du mécanisme : deploy/oto-mcp-bluegreen.sh.
#
# Usage : oto-backend-canari.sh            -> déploie origin/main
#         oto-backend-canari.sh --rollback -> rebascule sur la couleur inactive,
#                                             qui porte encore la version d'avant.
set -uo pipefail

BG_ENV=canari
BG_UNIT=oto-mcp-canari
BG_TREE=/opt/oto-mcp-canari
BG_PORT_blue=9105
BG_PORT_green=9108
BG_UPSTREAM=/etc/caddy/upstream-oto-canari.conf
BG_SNIPPET=oto_canari
BG_DOCSHARE_HOST=mcp.oto.ninja
BG_ASK=                      # l'on-demand TLS est porté par la prod uniquement
BG_ACTIVE=/etc/oto-mcp/active-canari
BG_PUBLIC="https://mcp-canari.oto.ninja/.well-known/oauth-authorization-server"
# Même plafond qu'en prod (cf. oto-backend.sh) : la vidange couvre les requêtes en
# cours, pas les sessions — inutile de laisser deux versions coexister plus longtemps.
BG_DRAIN_MAX=120

. /opt/deploy/oto-mcp-bluegreen.sh

bg_run "${1:-origin/main}"
