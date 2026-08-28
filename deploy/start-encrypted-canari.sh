#!/bin/bash
# MIROIR VERSIONNÉ du start-encrypted.sh de PRÉPRODUCTION (canari) sur oto-platform.
# Le fichier réel vit dans l'arbre de chaque couleur (/opt/oto-mcp-canari-blue/ et
# -green/), lancé par le gabarit systemd oto-mcp-canari@.service.
#
# ⚠️ Mêmes règles que la prod (cf. start-encrypted.sh) : indépendant du chemin, et
# propagé de la couleur en service vers la nouvelle plutôt que réinstallé d'ici.
#
# Diffère de la prod sur deux points, volontairement : PAS de clé Pennylane (le canari
# partage la base de la prod — une facture émise depuis la préprod serait une VRAIE
# facture) et le secret Mollie est celui de TEST.
set -e
set -a; . /etc/oto-mcp/scw.env; set +a

# Master key (chiffrement du coffre) — CRITIQUE : un échec abort le boot.
RESP=$(curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" \
  "https://api.scaleway.com/secret-manager/v1beta1/regions/fr-par/secrets/7def5e38-5a6f-4f31-b8cf-9f5d3f356cf0/versions/latest_enabled/access")
OTO_MCP_MASTER_KEY=$(printf '%s' "$RESP" | python3 -c "import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)['data']).decode())" | tr -d '\n')
[ ${#OTO_MCP_MASTER_KEY} -eq 64 ] || { echo "master key invalide (len ${#OTO_MCP_MASTER_KEY})" >&2; exit 1; }
export OTO_MCP_MASTER_KEY

# MOLLIE_API_KEY (billing par org, ADR 0043 — PSP Mollie) — BEST-EFFORT : un échec
# de fetch NE casse PAS le boot (le serveur vit sans billing, is_configured()=False).
set +e
MOLLIE_RESP=$(curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" \
  "https://api.scaleway.com/secret-manager/v1beta1/regions/fr-par/secrets/0953aad6-0706-42df-98eb-fe11e8aba089/versions/latest_enabled/access")
MOLLIE_API_KEY=$(printf '%s' "$MOLLIE_RESP" | python3 -c "import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)['data']).decode())" 2>/dev/null | tr -d '\n')
if [ -n "$MOLLIE_API_KEY" ]; then
  export MOLLIE_API_KEY
  echo "billing: MOLLIE_API_KEY résolue depuis Secret Manager" >&2
else
  echo "billing: MOLLIE_API_KEY non résolue — billing inactif (mollie_client no-op)" >&2
fi
set -e

# bleu/vert : on exécute le venv de NOTRE répertoire (même script pour les deux couleurs)
exec "$(cd "$(dirname "$0")" && pwd)/.venv/bin/oto-mcp"
