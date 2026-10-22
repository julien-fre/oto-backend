"""L'affichage d'une recette (docs/recettes.md §Affichage).

Une colonne NEUVE, nullable, sans défaut — le DDL est
`db/recipes.py::DDL_COLONNE_DISPLAY`, exécuté tel quel : `recipes.display JSONB`. Ce
qu'une page de processus montre à l'endroit où il lance la recette (ses étapes, ce que
chacune facture) ; NULL = rien d'écrit, l'état de toutes les recettes d'avant.

**Verrous.** `AccessExclusiveLock` bref sur `recipes` (colonne nullable sans défaut :
catalogue seul, aucune réécriture). `lock_timeout` 5 s : un échec net, qu'on rejoue,
plutôt qu'une attente qui bloquerait les lectures de recettes derrière elle.

**Ordre avec le code : indifférent.** Le code de ce lot lit la colonne
(`_RECIPE_COLS`), mais le démarrage la pose avant de servir (`ADD COLUMN IF NOT
EXISTS`, même DDL, sous garde de catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la colonne (les affichages écrits se perdent, les pages ne
montrent plus les étapes).

Révision : 20261022_2100_recipes_display
Précédente : 20261021_1352_outils_act_envois
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.recipes import COLONNE_DISPLAY, DDL_COLONNE_DISPLAY

revision = "20261022_2100_recipes_display"
down_revision = "20261021_1352_outils_act_envois"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DDL_COLONNE_DISPLAY)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE recipes DROP COLUMN IF EXISTS {COLONNE_DISPLAY}")
