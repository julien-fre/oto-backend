"""datastore_aliases : les anciens noms d'un tableau renommé, résolus en dernier recours.

Renommer un tableau cassait tout ce qui le désignait par son NOM sans pouvoir être
réécrit — corps de procédure, guide, prompt planifié, flux externe. Le renommage dépose
désormais l'ancien nom ici, et la résolution par nom le lit quand aucun tableau vivant
ne porte ce nom dans la portée de l'appelant (`db/datastore_ns.py`).

Le `CREATE TABLE` vit dans le fragment `db/schema/datastore.py` (`ALIASES`) et cette
révision l'exécute tel quel. Comme toute table neuve, il est aussi joué au démarrage
(`CREATE TABLE IF NOT EXISTS`, ADR 0065) : révision et boot sont idempotents l'un
envers l'autre. La clé étrangère vers `user_datastores` prend un verrou sur cette
table à la création seulement, d'où le `lock_timeout`.

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ignore la table : jouer cette
révision avant ou après le déploiement est sûr, l'ordre est indifférent (même régime
que 0004 et 0011). Un renommage fait par l'ancien code ne laisse simplement pas
d'alias. Le retour arrière retire la table et ses lignes : les anciens noms cessent de
résoudre, rien d'autre ne change.

Révision : 0027_alias_de_tableau
Précédente : 0026_transcription_tours
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.datastore import ALIASES

revision = "0027_alias_de_tableau"
down_revision = "0026_transcription_tours"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(ALIASES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS datastore_aliases")
