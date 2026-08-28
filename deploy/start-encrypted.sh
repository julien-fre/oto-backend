#!/bin/bash
# MIROIR VERSIONNÉ de /opt/oto-mcp/start-encrypted.sh (oto-platform, PRODUCTION).
# Le fichier réel n'est PAS suivi par git sur la box : il est lancé par le drop-in
# systemd oto-mcp.service.d/encryption.conf (ExecStart). Modifier ici, puis recopier
# sur la box — un `git reset --hard` de déploiement ne le touche pas.
# Le canari a SON PROPRE fichier, volontairement différent (pas de clé Pennylane).
set -e
set -a; . /etc/oto-mcp/scw.env; set +a

# Master key (chiffrement du coffre) — CRITIQUE : un échec abort le boot.
RESP=$(curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" \
  "https://api.scaleway.com/secret-manager/v1beta1/regions/fr-par/secrets/7def5e38-5a6f-4f31-b8cf-9f5d3f356cf0/versions/latest_enabled/access")
OTO_MCP_MASTER_KEY=$(printf '%s' "$RESP" | python3 -c "import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)['data']).decode())" | tr -d '\n')
[ ${#OTO_MCP_MASTER_KEY} -eq 64 ] || { echo "master key invalide (len ${#OTO_MCP_MASTER_KEY})" >&2; exit 1; }
export OTO_MCP_MASTER_KEY

# MOLLIE_API_KEY LIVE (billing par org, ADR 0043 — PSP Mollie) — BEST-EFFORT : un
# échec de fetch NE casse PAS le boot (le serveur vit sans billing, is_configured()=False).
# Secret SM LIVE (mollie-api-key-live), distinct du secret test du canari.
set +e
MOLLIE_RESP=$(curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" \
  "https://api.scaleway.com/secret-manager/v1beta1/regions/fr-par/secrets/436c993a-e87c-475f-809c-ff5ce270a84d/versions/latest_enabled/access")
MOLLIE_API_KEY=$(printf '%s' "$MOLLIE_RESP" | python3 -c "import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)['data']).decode())" 2>/dev/null | tr -d '\n')
if [ -n "$MOLLIE_API_KEY" ]; then
  export MOLLIE_API_KEY
  echo "billing: MOLLIE_API_KEY (live) résolue depuis Secret Manager" >&2
else
  echo "billing: MOLLIE_API_KEY non résolue — billing inactif (mollie_client no-op)" >&2
fi
set -e

# OTO_PENNYLANE_API_KEY (facturation des encaissements Mollie — factures d'Otomata
# au client, dans NOTRE comptabilité) — BEST-EFFORT comme MOLLIE : un échec de fetch
# NE casse PAS le boot ; les factures restent « en attente » et la reprise horaire les
# émet quand la clé revient.
# Secret SM : oto-pennylane-api-key-live (société OTOMATA, SIREN 106974637).
# PROD UNIQUEMENT — rien sur le canari : il partage la base de la prod, une facture
# émise depuis la préprod serait une VRAIE facture.
set +e
PL_RESP=$(curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" \
  "https://api.scaleway.com/secret-manager/v1beta1/regions/fr-par/secrets/11ca48fc-a59e-4097-8326-affb1c3a8228/versions/latest_enabled/access")
OTO_PENNYLANE_API_KEY=$(printf '%s' "$PL_RESP" | python3 -c "import json,sys,base64; print(base64.b64decode(json.load(sys.stdin)['data']).decode())" 2>/dev/null | tr -d '\n')
if [ -n "$OTO_PENNYLANE_API_KEY" ]; then
  export OTO_PENNYLANE_API_KEY
  echo "billing: OTO_PENNYLANE_API_KEY résolue depuis Secret Manager" >&2
else
  echo "billing: OTO_PENNYLANE_API_KEY non résolue — factures en attente (reprise horaire)" >&2
fi
set -e

exec /opt/oto-mcp/.venv/bin/oto-mcp
