"""Qui paie une conversation de l'agent intégré que l'opérateur parraine (le Post
studio, `access/parrainage.py`, docs/copilot.md §Conversations parrainées). Une colonne
NEUVE, nullable, sans défaut — le DDL est `db/copilot.py::DDL_COLONNE_SPONSOR`, exécuté
tel quel : `copilot_threads.sponsor TEXT`. NULL = l'org, comme toujours. Et ce qu'une
conversation parrainée a acheté aux fournisseurs payants, par famille (`DDL_COLONNE_
USAGES_PARRAINES` : `copilot_threads.sponsor_uses JSONB`, même règle).

**Verrous.** `AccessExclusiveLock` bref sur `copilot_threads` (colonne nullable sans
défaut : catalogue seul, aucune réécriture). `lock_timeout` 5 s, comme les autres
révisions.

**Ordre avec le code : indifférent.** Le démarrage pose la même colonne (`ADD COLUMN IF
NOT EXISTS`, même DDL, sous garde de catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la colonne (les conversations parrainées en cours redeviennent
celles de leur org).

Révision : 20261119_1700_copilot_sponsor
Précédente : 20261119_1200_slack_mention_dflt
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db import copilot

revision = "20261119_1700_copilot_sponsor"
down_revision = "20261119_1200_slack_mention_dflt"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(copilot.DDL_COLONNE_SPONSOR)
    op.execute(copilot.DDL_COLONNE_USAGES_PARRAINES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE copilot_threads DROP COLUMN IF EXISTS "
               f"{copilot.COLONNE_USAGES_PARRAINES}")
    op.execute(f"ALTER TABLE copilot_threads DROP COLUMN IF EXISTS {copilot.COLONNE_SPONSOR}")
