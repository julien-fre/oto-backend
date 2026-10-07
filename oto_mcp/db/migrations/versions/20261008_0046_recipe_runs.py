"""recipe_leases, recipe_pending_jobs, recipe_schedules : ce que l'exécution d'une recette tient.

Le bail par tableau (deux exécutions simultanées sur les mêmes lignes paieraient deux
fois ou créeraient deux fois une fiche chez un tiers), le travail asynchrone en attente
d'une recette `pull` (un lancement Apify payé ne se relance pas), et les exécutions
programmées (`docs/recettes.md`).

Trois tables NEUVES, nées entières, deux clés étrangères vers `recipes` (petite) et un
index partiel : aucune réécriture, aucun parcours d'une table servie. Le DDL est le
fragment `db/schema/recipes.py::RECIPE_RUNS`, exécuté tel quel.

**Ordre avec le code : indifférent.** Le démarrage pose les mêmes tables (`CREATE TABLE
IF NOT EXISTS`, même fragment). L'ancien code les ignore.

⚠️ Numéro PROVISOIRE : une autre PR ouverte prend 0044. Au merge, poser `down_revision`
sur la tête du moment et renuméroter si besoin.

Retour arrière : retire les trois tables — les programmes et les travaux en attente sont
perdus (un lancement Apify en cours reste chez Apify, payé).

Révision : 0046_recipe_runs
Précédente : 0045_jetons_emetteurs
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.recipes import RECIPE_RUNS

revision = "0046_recipe_runs"
down_revision = "0045_jetons_emetteurs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(RECIPE_RUNS)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recipe_schedules")
    op.execute("DROP TABLE IF EXISTS recipe_pending_jobs")
    op.execute("DROP TABLE IF EXISTS recipe_leases")
