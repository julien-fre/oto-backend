"""origine_ecritures retirée : le relevé du préavis de la couche `origine` a fini de servir (#1109).

`origine_ecritures` comptait, par (écrivain, tableau, colonne), qui posait la couche
`origine` d'une cellule, déclarée ou non, pour dimensionner le préavis d'oto#70 lot 2. Le
préavis est clos : poser l'origine sans la déclarer est refusé, et le journal de
production ne montre aucun refus sur les quatorze jours précédant ce retrait. Plus aucun
code servi ne lit ni n'écrit la table ; le refus, lui, reste (`datastore/controles.py`).

**Ce qu'elle fait.** Elle lit le catalogue (`to_regclass`, aucun verrou sur la table) et
ne lance `DROP TABLE IF EXISTS origine_ecritures` que si la table est là ; ses deux index
partent avec elle. Sur une base NEUVE (le démarrage ne la crée plus), elle ne fait rien :
aucun ordre, aucun verrou. Rejouée, elle ne fait rien non plus. L'essai à blanc (`--sql`),
sans base à lire, imprime l'ordre.

**Verrou** (seulement là où la table existe) : `AccessExclusiveLock` sur
`origine_ecritures`, bref — une écriture de catalogue et la suppression de ses fichiers.
Aucune autre table n'est touchée (ni clé étrangère vers elle, ni depuis elle). L'attente
est bornée par `lock_timeout` 5 s : une transaction qui tient encore la table fait
échouer la révision plutôt que d'empiler des requêtes derrière elle (la rejouer suffit).

**Ordre avec le code : APRÈS le tag.** L'ancien code écrit la table à chaque écriture
d'origine déclarée (en best-effort : il journaliserait un avertissement, sans échouer),
et son démarrage la recrée (`CREATE TABLE IF NOT EXISTS`, puis deux `ALTER`). Jouée
avant que la production et la préproduction (base partagée) servent ce code, un
redémarrage de l'ancien la reposerait vide, alors que la base se dirait déjà en `0048`.

**Retour arrière : refusé.** La table est morte, ses compteurs n'ont plus d'usage ; la
recréer vide ne rendrait rien. `downgrade` lève.

Révision : 0048_origine_ecritures_retiree
Précédente : 0047_cles_d_org
"""
from __future__ import annotations

from alembic import context, op
from sqlalchemy import text

revision = "0048_origine_ecritures_retiree"
down_revision = "0047_cles_d_org"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"

#: Le constat, au catalogue : `to_regclass` rend NULL sans lever ni verrouiller.
_TABLE_PRESENTE = text("SELECT to_regclass('origine_ecritures') IS NOT NULL")

DDL = "DROP TABLE IF EXISTS origine_ecritures"


def upgrade() -> None:
    # L'essai à blanc (`--sql`) n'a pas de base à lire : il imprime l'ordre tel qu'il
    # partirait sur une base qui porte encore la table.
    if not context.is_offline_mode() and \
            not op.get_bind().execute(_TABLE_PRESENTE).scalar():
        return
    op.execute(_ATTENTE_MAX)
    op.execute(DDL)


def downgrade() -> None:
    raise RuntimeError(
        "0048_origine_ecritures_retiree est irréversible : `origine_ecritures` n'a plus "
        "ni lecteur ni écrivain, la recréer ne rendrait aucune donnée. Pour descendre "
        "sous 0048, `alembic stamp 0047_cles_d_org` (la table absente ne gêne "
        "aucune révision antérieure).")
