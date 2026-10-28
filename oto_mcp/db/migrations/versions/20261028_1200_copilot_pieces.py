"""Les fichiers joints aux messages de l'agent intégré (docs/copilot.md §Pièces jointes).

Une table NEUVE, née entière, et son index — le DDL est le fragment
`db/schema/copilot.py::PIECES`, exécuté tel quel (une seule écriture) :
`copilot_attachments (id, sub, org_id, s3_key, name, media_type, size, created_at)` et
l'index `(created_at)` que lit la purge à 30 jours.

**Verrous.** `AccessExclusiveLock` sur la table neuve (vide, instantané) ; aucune clé
étrangère, aucune table existante touchée. `lock_timeout` 5 s, comme les autres
révisions.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE
IF NOT EXISTS`, même fragment) ; l'ancien code l'ignore.

Retour arrière : retire la table (les pièces déjà jointes ne se résolvent plus : le
modèle lit à leur place qu'un fichier a été montré ; les objets restent dans le
stockage jusqu'à leur purge).

Révision : 20261028_1200_copilot_pieces
Précédente : 20261027_1800_row_comments
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.copilot import PIECES

revision = "20261028_1200_copilot_pieces"
down_revision = "20261027_1800_row_comments"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(PIECES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS copilot_attachments")
