"""Point de départ — l'état du schéma au 14/09/2026.

Cette révision est **vide, et c'est voulu**.

La base est vivante : son schéma existe déjà. On ne le rejoue pas, on le déclare —
la commande `alembic stamp head`, lancée une seule fois, écrit dans la base que ce
point est atteint. Tout ce qui suivra partira de là.

Et une base NEUVE ? Elle reçoit son schéma du démarrage, comme aujourd'hui : la
construction additive (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`)
reste au boot, c'est ce qui a été décidé. Ce registre ne porte QUE ce que le boot ne
sait pas faire : renommer, recopier, supprimer, remplir une colonne depuis une autre.

Révision : 0001_point_de_depart
Précédente : aucune
"""
from __future__ import annotations

revision = "0001_point_de_depart"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
