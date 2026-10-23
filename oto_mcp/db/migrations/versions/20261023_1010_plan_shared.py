"""Le prêt du forfait d'un agent à son équipe (docs/runner-et-automatisations.md, § Un
agent d'abonnement).

Une colonne NEUVE, booléenne, défaut `false` — le DDL est
`db/runner_triggers.py::DDL_COLONNE_PLAN_PARTAGE`, exécuté tel quel :
`runner_triggers.plan_shared BOOLEAN NOT NULL DEFAULT false`. Vraie = le propriétaire
prête son abonnement personnel à toute l'org qui peut modifier l'agent ; `false` =
l'état de tous les agents d'avant (seuls les éditeurs qu'il a nommés l'empruntent).

**Verrous.** `AccessExclusiveLock` bref sur `runner_triggers` (défaut constant : catalogue
seul depuis PG 11, aucune réécriture). `lock_timeout` 5 s : le tick lit la table en
continu ; un échec net, qu'on rejoue, plutôt qu'une attente qui bloquerait ses lectures.

**Ordre avec le code : indifférent.** Le code de ce lot lit la colonne (`_COLS`,
`_forfait_prete`), mais le démarrage la pose avant de servir (`ADD COLUMN IF NOT EXISTS`,
même DDL, sous garde de catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la colonne — chaque prêt à l'équipe se perd, les éditeurs nommés
gardent le leur.

Révision : 20261023_1010_plan_shared
Précédente : 20261023_1000_agents_org_default
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.runner_triggers import COLONNE_PLAN_PARTAGE, DDL_COLONNE_PLAN_PARTAGE

revision = "20261023_1010_plan_shared"
down_revision = "20261023_1000_agents_org_default"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DDL_COLONNE_PLAN_PARTAGE)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE runner_triggers DROP COLUMN IF EXISTS {COLONNE_PLAN_PARTAGE}")
