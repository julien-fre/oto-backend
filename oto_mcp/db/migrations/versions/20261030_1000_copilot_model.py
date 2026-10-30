"""Le modèle qui répond à une conversation de l'agent intégré (docs/copilot.md §Quel
modèle répond). Une colonne NEUVE, nullable, sans défaut — le DDL est
`db/copilot.py::DDL_COLONNE_MODELE`, exécuté tel quel :
`copilot_threads.model TEXT`. NULL = une conversation d'avant : elle garde le modèle et
la liste d'outils de son début (les changer sous elle invaliderait son cache et, sur ces
modèles, son raisonnement antérieur).

**Verrous.** `AccessExclusiveLock` bref sur `copilot_threads` (colonne nullable sans
défaut : catalogue seul, aucune réécriture). `lock_timeout` 5 s, comme les autres
révisions.

**Ordre avec le code : indifférent.** Le démarrage pose la même colonne (`ADD COLUMN IF
NOT EXISTS`, même DDL, sous garde de catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la colonne (les conversations en cours reprennent sur le modèle
d'avant, au prix d'un cache refroidi).

Révision : 20261030_1000_copilot_model
Précédente : 20261029_1705_provider_credits
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db import copilot

revision = "20261030_1000_copilot_model"
down_revision = "20261029_1705_provider_credits"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(copilot.DDL_COLONNE_MODELE)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE copilot_threads DROP COLUMN IF EXISTS {copilot.COLONNE_MODELE}")
