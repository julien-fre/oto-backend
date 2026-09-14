#!/usr/bin/env bash
# Éprouve l'outil de migration SUR UNE VRAIE BASE, jetable — jamais sur la nôtre.
#
# Pourquoi ce banc existe : l'essai à blanc (`alembic upgrade head --sql`) n'ouvre
# aucune connexion. Il a montré un SQL parfait alors que l'application réelle
# n'écrivait rien, sortait zéro, et laissait la base intacte. Un succès déguisé ne se
# voit que contre un vrai PostgreSQL.
#
#   ./scripts/essai_migrations.sh        (demande docker ; ne touche à aucune base réelle)
set -euo pipefail
cd "$(dirname "$0")/.."

NOM=pg-essai-migrations
PORT=${PORT_ESSAI:-55432}
CLE=$(python3 -c "print(0x07050065)")   # la clé du verrou, lue dans le code, pas de tête
export DATABASE_URL="postgresql://postgres:essai@127.0.0.1:${PORT}/oto_essai"
A=".venv/bin/python -m alembic"
echec=0
dire() { printf '%s %s\n' "$1" "$2"; }

nettoyer() { docker rm -f "$NOM" >/dev/null 2>&1 || true; rm -f oto_mcp/db/migrations/versions/*_essai_jetable.py; }
trap nettoyer EXIT

docker rm -f "$NOM" >/dev/null 2>&1 || true
docker run -d --rm --name "$NOM" -e POSTGRES_PASSWORD=essai -e POSTGRES_DB=oto_essai \
  -p "${PORT}:5432" postgres:17 >/dev/null
for _ in $(seq 1 30); do docker exec "$NOM" pg_isready -q 2>/dev/null && break; sleep 1; done
psql_essai() { docker exec "$NOM" psql -U postgres -d oto_essai -tAc "$1"; }

# 1. l'application réelle écrit vraiment
$A upgrade head >/dev/null
[ "$(psql_essai "select count(*) from alembic_version")" = "1" ] \
  && dire ✓ "le registre est écrit dans la base" \
  || { dire ✗ "RIEN n'a été écrit — le succès déguisé est de retour"; echec=1; }

# 2. une vraie migration s'applique et se défait
cat > oto_mcp/db/migrations/versions/99999999_zz_essai_jetable.py <<'PY'
"""Essai jetable — une migration en SQL, appliquée puis défaite."""
from __future__ import annotations
from alembic import op
revision = "zz_essai_jetable"
down_revision = "0001_point_de_depart"
branch_labels = None
depends_on = None
def upgrade() -> None: op.execute("CREATE TABLE essai_jetable (id BIGINT PRIMARY KEY)")
def downgrade() -> None: op.execute("DROP TABLE essai_jetable")
PY
$A upgrade head >/dev/null
[ "$(psql_essai "select to_regclass('essai_jetable') is not null")" = "t" ] \
  && dire ✓ "une migration s'applique" || { dire ✗ "la migration n'a rien créé"; echec=1; }
$A downgrade -1 >/dev/null
[ "$(psql_essai "select to_regclass('essai_jetable') is null")" = "t" ] \
  && dire ✓ "et elle se défait" || { dire ✗ "le retour arrière n'a rien retiré"; echec=1; }
rm -f oto_mcp/db/migrations/versions/99999999_zz_essai_jetable.py

# 3. le verrou mord : une migration concurrente attend
docker exec -d "$NOM" bash -c "psql -U postgres -d oto_essai -c \"select pg_advisory_lock($CLE); select pg_sleep(30);\""
sleep 2
[ "$(psql_essai "select count(*) from pg_locks where locktype='advisory'")" = "1" ] \
  || { dire ✗ "le verrou d'essai n'est pas tenu — le reste ne prouverait rien"; exit 1; }
set +e; timeout 8 $A upgrade head >/dev/null 2>&1; code=$?; set -e
[ "$code" = "124" ] && dire ✓ "une migration concurrente ATTEND le verrou" \
  || { dire ✗ "elle n'attend pas (code $code) — deux migrations peuvent se marcher dessus"; echec=1; }
psql_essai "select pg_terminate_backend(pid) from pg_stat_activity where query ilike '%pg_sleep%' and pid <> pg_backend_pid()" >/dev/null
sleep 1
timeout 20 $A upgrade head >/dev/null 2>&1 \
  && dire ✓ "et elle passe dès que le verrou est rendu" \
  || { dire ✗ "elle ne repart pas après le verrou"; echec=1; }

exit $echec
