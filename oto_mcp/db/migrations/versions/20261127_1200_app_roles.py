"""Le rôle d'APP d'un partage (`docs/apps.md` § Rôles d'une app) : `resource_grants.app_role`,
et l'accès à un tableau À TRAVERS une app seulement : la table `app_table_access`
(fragment `ACCES_PAR_L_APP`, `CREATE TABLE IF NOT EXISTS` — neuve, vide, sans verrou
sur l'existant hors les FK vers `apps` et `user_datastores`, un `ShareRowExclusiveLock`
bref).

Deux colonnes NEUVES, nullables, sans défaut — le DDL est le fragment
`db/schema/apps.py::ROLES`, exécuté tel quel (`ADD COLUMN IF NOT EXISTS`). `app_role` :
la clé d'un des rôles que le manifeste d'une app déclare (`manifest.roles`) ; NULL = le
rôle par défaut du manifeste, LU comme tel (rien n'est recopié : l'état de toutes les
lignes d'avant, et de tous les partages qui ne sont pas d'une app). `app_scope` (JSONB)
: le périmètre de lignes du partage (`docs/apps.md` § Périmètre de lignes) ; NULL =
aucun.

**Verrous.** Un `AccessExclusiveLock` bref sur `resource_grants` (colonne nullable sans
défaut : catalogue seul, aucune réécriture). `lock_timeout` 5 s : la table est lue à
chaque jugement d'accès ; un échec net, qu'on rejoue, plutôt qu'une attente qui
bloquerait ces lectures derrière elle.

**Ordre avec le code : indifférent.** Le démarrage pose la colonne avant de servir ;
l'ancien code l'ignore (ses `INSERT … ON CONFLICT` ne la nomment pas : un repartage
la garde). Une app d'avant ne déclare aucun rôle : rien ne change pour elle.

Retour arrière : retire les colonnes (chaque lecteur revient au rôle par défaut de
l'app ; un périmètre de lignes posé sur un partage se perd — ⚠️ ce lecteur verrait
alors TOUTES les lignes que son rôle ouvre : retirer d'abord son partage)
et la table (les accès par l'app se perdent : ces lecteurs ne lisent plus ces tableaux).

Révision : 20261127_1200_app_roles
Précédente : 20261125_1700_responsables
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.apps import ACCES_PAR_L_APP, ROLES

revision = "20261127_1200_app_roles"
down_revision = "20261125_1700_responsables"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(ROLES)
    op.execute(ACCES_PAR_L_APP)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS app_table_access")
    op.execute("ALTER TABLE resource_grants DROP COLUMN IF EXISTS app_scope")
    op.execute("ALTER TABLE resource_grants DROP COLUMN IF EXISTS app_role")
