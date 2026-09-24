"""datastore_row_revisions.suppression : la suppression d'une ligne devient une révision (oto#273).

Une colonne `BOOLEAN NOT NULL DEFAULT false` : toutes les révisions existantes sont des
écritures, et `false` est leur vérité. Pour PostgreSQL (≥ 11), un défaut constant est
une écriture de catalogue seule, sans réécriture ni parcours de la table.

Mais l'`ALTER` prend un `AccessExclusiveLock` sur le journal, que CHAQUE écriture de
ligne alimente par son déclencheur : d'où le `lock_timeout` — un échec net, qu'on
rejoue, plutôt qu'une file d'écritures bloquées derrière nous. Le démarrage pose la
même colonne s'il ne la trouve pas (`journal_revisions.DDL_COLONNE_SUPPRESSION`, sous
garde de catalogue, dans la transaction du boot et son `lock_timeout`), AVANT la
fonction et le déclencheur de suppression qui l'écrivent : révision et boot sont
idempotents l'un envers l'autre, l'ordre entre eux est indifférent. Une base NEUVE la
reçoit du `CREATE TABLE` (`db/schema/datastore.py::REVISIONS`).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code n'écrit pas la colonne (ses
révisions prennent le défaut, `false` : ce sont des écritures) et ne la lit pas :
jouer cette révision avant ou après le déploiement est sûr. Le retour arrière retire
d'abord le déclencheur et la fonction de suppression, qui écriraient sinon une colonne
absente — chaque suppression de ligne échouerait —, puis la colonne ; les révisions de
suppression deviennent alors indiscernables des écritures.

Révision : 0016_journal_suppression
Précédente : 0015_droits_valeur_obligatoire
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.journal_revisions import (
    COLONNE_SUPPRESSION, DDL_COLONNE_SUPPRESSION, DECLENCHEUR_DELETE,
    NOM_FONCTION_SUPPRESSION, TABLE)

revision = "0016_journal_suppression"
down_revision = "0015_droits_valeur_obligatoire"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DDL_COLONNE_SUPPRESSION)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"DROP TRIGGER IF EXISTS {DECLENCHEUR_DELETE} ON datastore_rows")
    op.execute(f"DROP FUNCTION IF EXISTS {NOM_FONCTION_SUPPRESSION}()")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {COLONNE_SUPPRESSION}")
