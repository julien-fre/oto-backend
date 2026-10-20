"""Les outils d'admin qu'un agent exerce avec le rôle plateforme de son porteur (revue de #1274).

Une colonne NEUVE, au défaut constant — le DDL est
`db/runner_triggers.py::DDL_COLONNE_OUTILS_PLATEFORME`, exécuté tel quel :
`runner_triggers.outils_plateforme JSONB NOT NULL DEFAULT '[]'`. La liste BORNÉE des
`oto_admin_*` qu'un agent hébergé garde sous `OTO_VERROU_ROLES_PLATEFORME=enforce`
(`verrou_org.outils_permis`) ; `[]` = aucun, l'état de tous les agents d'avant.

**Verrous.** `AccessExclusiveLock` bref sur `runner_triggers` (défaut constant depuis
PostgreSQL 11 : catalogue seul, aucune réécriture). `lock_timeout` 5 s : le tick lit la
table en continu ; un échec net, qu'on rejoue, plutôt qu'une attente qui bloquerait ses
lectures derrière elle.

**Ordre avec le code : indifférent.** Le code de ce lot lit la colonne (`_COLS`), mais le
démarrage la pose avant de servir (`ADD COLUMN IF NOT EXISTS`, même DDL, sous garde de
catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la colonne (les listes posées se perdent : les agents retombent
sous le verrou pour tous leurs outils d'admin).

Révision : 0068_triggers_outils_plateforme
Précédente : 0067_runner_jobs_declencheurs
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.runner_triggers import (COLONNE_OUTILS_PLATEFORME,
                                        DDL_COLONNE_OUTILS_PLATEFORME)

revision = "0068_triggers_outils_plateforme"
down_revision = "0067_runner_jobs_declencheurs"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DDL_COLONNE_OUTILS_PLATEFORME)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE runner_triggers DROP COLUMN IF EXISTS {COLONNE_OUTILS_PLATEFORME}")
