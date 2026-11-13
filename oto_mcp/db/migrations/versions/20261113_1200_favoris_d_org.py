"""Les favoris partagés d'une org : pages et tableaux épinglés pour tous ses membres.

Une table NEUVE, née entière : `org_favorites` (`db/schema/orgs.py::ORG_FAVORITES`) —
posée aussi par le démarrage (`CREATE TABLE IF NOT EXISTS` de l'assemblage), même DDL.
FK vers `orgs` (ON DELETE CASCADE) ; aucune FK vers la page ou le tableau épinglé.

**Verrous.** La table est neuve et vide ; la FK prend un `ShareRowExclusiveLock` bref sur
`orgs`. `lock_timeout` 5 s : un échec net, qu'on rejoue, plutôt qu'une attente.

**Ordre avec le code : indifférent.** Le démarrage pose la même table ; l'ancien code
l'ignore.

Retour arrière : retire la table (les favoris se perdent ; rien d'autre ne les lit).

Révision : 20261113_1200_favoris_d_org
Précédente : 20261112_1200_connecteur_api
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.orgs import ORG_FAVORITES

revision = "20261113_1200_favoris_d_org"
down_revision = "20261112_1200_connecteur_api"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(ORG_FAVORITES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS org_favorites")
