"""user_datastores.context_org_id : l'org où un tableau personnel a été créé (oto#160).

Une colonne `BIGINT` NULLABLE, clé étrangère vers `orgs` en `ON DELETE SET NULL` : une
org supprimée laisse le tableau à son propriétaire, sans contexte. Sans défaut, donc
écriture de catalogue seule ; la clé étrangère se valide sur une colonne entièrement
NULL, sans rien à comparer.

L'`ALTER` prend un `AccessExclusiveLock` sur `user_datastores`, que toute résolution de
tableau lit, et un verrou sur `orgs` pour la clé étrangère : d'où le `lock_timeout` — un
échec net, qu'on rejoue, plutôt qu'une file de lectures bloquées derrière nous. Le
démarrage pose la même colonne s'il ne la trouve pas (`datastore_ns.DDL_COLONNE_CONTEXTE_ORG`,
sous garde de catalogue, dans la transaction du boot et son `lock_timeout`) : révision
et boot sont idempotents l'un envers l'autre, l'ordre entre eux est indifférent. Une
base NEUVE la reçoit du `CREATE TABLE` (`db/schema/datastore.py::DATASTORE`).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit pas la colonne et ne
l'écrit pas (ses créations la laissent NULL, comme un tableau d'avant elle) : jouer
cette révision avant ou après le déploiement est sûr. Le retour arrière retire la
colonne et ce qu'elle savait ; le code qui l'écrit doit être retiré AVANT, sinon chaque
création de tableau échoue.

Révision : 0017_tableaux_contexte_org
Précédente : 0016_journal_suppression
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.datastore_ns import COLONNE_CONTEXTE_ORG, DDL_COLONNE_CONTEXTE_ORG

revision = "0017_tableaux_contexte_org"
down_revision = "0016_journal_suppression"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DDL_COLONNE_CONTEXTE_ORG)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE user_datastores DROP COLUMN IF EXISTS {COLONNE_CONTEXTE_ORG}")
