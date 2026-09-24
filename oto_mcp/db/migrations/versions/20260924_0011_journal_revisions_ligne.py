"""datastore_row_revisions : le journal des révisions de ligne, en écriture fantôme (oto#273 M1).

Chaque écriture d'une ligne de tableau y dépose son diff, valeurs comprises
(`{champ: {avant, apres}}`), écrit par un déclencheur AFTER sur `datastore_rows`.
Rien ne le lit encore : M1 mesure le volume.

Le `CREATE TABLE` vit dans le fragment `db/schema/datastore.py` (`REVISIONS`) et cette
révision l'exécute tel quel. Comme toute table neuve, il est aussi joué au démarrage
(`CREATE TABLE IF NOT EXISTS`, ADR 0065) : révision et boot sont idempotents l'un
envers l'autre, l'ordre entre eux est indifférent. La fonction et les déclencheurs,
eux, sont posés au boot (`db/journal_revisions.py`) : ils suivent le code servi. La clé
étrangère vers `user_datastores` prend un verrou sur cette table à la création
seulement, d'où le `lock_timeout`.

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ignore la table : jouer cette
révision avant ou après le déploiement est sûr. Le retour arrière (`downgrade`) retire
d'abord les déclencheurs et la fonction, qui écriraient sinon dans une table absente,
puis la table ET ses lignes.

Révision : 0011_journal_revisions_ligne
Précédente : 0010_tool_calls_result_shape
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.journal_revisions import (
    DECLENCHEUR_DELETE, DECLENCHEUR_INSERT, DECLENCHEUR_UPDATE, NOM_FONCTION,
    NOM_FONCTION_SUPPRESSION)
from oto_mcp.db.schema.datastore import REVISIONS

revision = "0011_journal_revisions_ligne"
down_revision = "0010_tool_calls_result_shape"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(REVISIONS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    # Le déclencheur de suppression aussi (posé par le démarrage depuis `0016`) : sur
    # une base que le boot a rattrapée, il survivrait sinon à la table qu'il écrit.
    for declencheur in (DECLENCHEUR_INSERT, DECLENCHEUR_UPDATE, DECLENCHEUR_DELETE):
        op.execute(f"DROP TRIGGER IF EXISTS {declencheur} ON datastore_rows")
    for fonction in (NOM_FONCTION, NOM_FONCTION_SUPPRESSION):
        op.execute(f"DROP FUNCTION IF EXISTS {fonction}()")
    op.execute("DROP TABLE IF EXISTS datastore_row_revisions")
