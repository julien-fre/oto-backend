"""L'agent hébergé qui a déclaré un passage (docs/runner-et-automatisations.md).

Une colonne NEUVE, nullable, sans défaut — le DDL est
`db/runner_fleets.py::DDL_COLONNE_TRIGGER_ID`, exécuté tel quel :
`runner_fleets.trigger_id BIGINT`. Posée par `oto_fleet op=create` quand l'appel vient
du run d'un agent hébergé ; NULL = une personne, ou un passage d'avant. L'écran des
agents s'en sert pour ranger un passage sous l'agent qui l'a lancé, même quand ce
passage joue une autre procédure que lui.

**Verrous.** `AccessExclusiveLock` bref sur `runner_fleets` (colonne nullable sans
défaut : catalogue seul, aucune réécriture). `lock_timeout` 5 s : chaque sondage d'un
worker lit cette table ; un échec net, qu'on rejoue, plutôt qu'une attente qui
bloquerait les sondages derrière elle.

**Ordre avec le code : indifférent.** Le code de ce lot lit et écrit la colonne
(`_COLS`, `create_fleet`), mais le démarrage la pose avant de servir (`ADD COLUMN IF
NOT EXISTS`, même DDL, sous garde de catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la colonne (les liens posés se perdent ; l'écran retombe sur
l'appariement par procédure).

Révision : 20261023_0900_fleet_trigger
Précédente : 20261022_2112_forfait_fenetre
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.runner_fleets import COLONNE_TRIGGER_ID, DDL_COLONNE_TRIGGER_ID

revision = "20261023_0900_fleet_trigger"
down_revision = "20261022_2112_forfait_fenetre"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DDL_COLONNE_TRIGGER_ID)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE runner_fleets DROP COLUMN IF EXISTS {COLONNE_TRIGGER_ID}")
