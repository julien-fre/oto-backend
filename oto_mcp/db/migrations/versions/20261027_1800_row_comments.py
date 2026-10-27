"""Les commentaires des personnes sur une ligne de tableau (docs/datastore.md
§Commentaires).

Une table NEUVE, née entière, et son index — le DDL est le fragment
`db/schema/datastore.py::COMMENTAIRES`, exécuté tel quel (une seule écriture) :
`datastore_row_comments (id, ns_id → user_datastores ON DELETE CASCADE, row_id,
auteur_sub, body, created_at, deleted_at)`, index `(ns_id, row_id, id)` et
`(auteur_sub)` (la fusion de comptes repointe cette colonne).

**Verrous.** `AccessExclusiveLock` sur la table neuve (vide, instantané) ; la clé
étrangère prend un `ShareRowExclusiveLock` sur `user_datastores`, le temps de poser la
contrainte sur une table vide. `datastore_rows` n'est pas touchée. `lock_timeout` 5 s,
comme les autres révisions.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE
IF NOT EXISTS`, même fragment) ; l'ancien code l'ignore.

Retour arrière : retire la table (les commentaires se perdent ; les lignes et leur
journal restent entiers).

Révision : 20261027_1800_row_comments
Précédente : 20261027_1300_agent_episodes
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.datastore import COMMENTAIRES

revision = "20261027_1800_row_comments"
down_revision = "20261027_1300_agent_episodes"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(COMMENTAIRES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS datastore_row_comments")
