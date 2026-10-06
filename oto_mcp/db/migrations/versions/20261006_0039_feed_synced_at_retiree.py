"""feed_synced_at retirée : le DROP qu'un commentaire de démarrage laissait à la main (#1162).

`unipile_accounts.feed_synced_at` (fraîcheur du miroir `linkedin-feed`) n'a plus ni
lecteur ni écrivain depuis que le feed LinkedIn est servi en direct (oto#156). Son
`DROP` n'avait été écrit nulle part : un commentaire de `db/_init.py` le confiait à
« l'opérationnel », qui l'a joué à la main sur la base partagée. Une instance née avant
ce geste a donc gardé la colonne, et un import de périmètre vers elle a refusé des
« colonnes différentes ». Cette révision rejoue le retrait partout où il manque.

**Ce qu'elle fait.** Elle lit le catalogue (`pg_attribute`, aucun verrou sur la table)
et ne lance `ALTER TABLE unipile_accounts DROP COLUMN IF EXISTS feed_synced_at` que si
la colonne est là. Sur la base partagée, déjà nettoyée à la main, et sur une base
NEUVE (le `CREATE TABLE` de `db/schema/unipile.py` ne la déclare plus), elle ne fait
rien : aucun ordre, aucun verrou. Rejouée, elle ne fait rien non plus. Une table
`unipile_accounts` absente lève (`regclass`) : ce n'est pas un état à taire. L'essai à
blanc (`--sql`), sans base à lire, imprime l'ordre.

**Verrou** (seulement là où la colonne existe) : `AccessExclusiveLock` sur
`unipile_accounts`, bref — un `DROP COLUMN` est une écriture de catalogue, sans
réécriture de la table. L'attente est bornée par `lock_timeout` 5 s : la table est lue
à chaque appel LinkedIn, une lecture longue en cours fait échouer la révision plutôt
que d'empiler les requêtes derrière elle (la rejouer suffit).

**Ordre avec le code : indifférent.** Aucun code servi, ancien ou nouveau, ne lit ni
n'écrit la colonne.

**Retour arrière : refusé.** La colonne est morte, ses valeurs n'ont plus de sens ; la
recréer vide ne rendrait rien et rouvrirait l'écart entre bases. `downgrade` lève.

Révision : 0039_feed_synced_at_retiree
Précédente : 0038_abandon_run
"""
from __future__ import annotations

from alembic import context, op
from sqlalchemy import text

revision = "0039_feed_synced_at_retiree"
down_revision = "0038_abandon_run"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"

#: Le constat, au catalogue : `regclass` lève si la table manque.
_COLONNE_PRESENTE = text(
    "SELECT 1 FROM pg_attribute WHERE attrelid = 'unipile_accounts'::regclass "
    "AND attname = 'feed_synced_at' AND NOT attisdropped")

DDL = "ALTER TABLE unipile_accounts DROP COLUMN IF EXISTS feed_synced_at"


def upgrade() -> None:
    # L'essai à blanc (`--sql`) n'a pas de base à lire : il imprime l'ordre tel qu'il
    # partirait sur une base qui porte encore la colonne.
    if not context.is_offline_mode() and \
            op.get_bind().execute(_COLONNE_PRESENTE).first() is None:
        return
    op.execute(_ATTENTE_MAX)
    op.execute(DDL)


def downgrade() -> None:
    raise RuntimeError(
        "0039_feed_synced_at_retiree est irréversible : `unipile_accounts.feed_synced_at` "
        "n'a plus ni lecteur ni écrivain, la recréer ne rendrait aucune donnée. Pour "
        "descendre sous 0039, `alembic stamp 0038_abandon_run` (la colonne absente ne "
        "gêne aucune révision antérieure).")
