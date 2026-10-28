"""Les demandes d'accès d'une personne bloquée, et leur décision
(docs/demandes-d-acces.md).

Une table NEUVE, née entière, et ses index — le DDL est le fragment
`db/schema/grants.py::DEMANDES_D_ACCES`, exécuté tel quel (une seule écriture) :
`access_requests (id, org_id → orgs ON DELETE CASCADE, requester_sub, kind, cible,
cle_cible, demande, note, status, decided_by, decided_at, decision, decision_note,
created_at, reminded_at, expires_at)`, index unique partiel « une demande en attente
par (demandeur, geste, cible) », index partiel des demandes en attente d'une org, et
`(requester_sub)` / `(decided_by)` (la fusion de comptes repointe ces colonnes).

**Verrous.** `AccessExclusiveLock` sur la table neuve (vide, instantané) ; la clé
étrangère prend un `ShareRowExclusiveLock` sur `orgs`, le temps de poser la contrainte
sur une table vide. `lock_timeout` 5 s, comme les autres révisions.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE
IF NOT EXISTS`, même fragment) ; l'ancien code l'ignore.

Retour arrière : retire la table (les demandes se perdent ; aucun accès accordé
n'est retiré — il vit dans les tables du geste qui l'a accordé).

Révision : 20261028_1900_access_requests
Précédente : 20261027_1800_row_comments
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.grants import DEMANDES_D_ACCES

revision = "20261028_1900_access_requests"
down_revision = "20261027_1800_row_comments"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DEMANDES_D_ACCES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS access_requests")
