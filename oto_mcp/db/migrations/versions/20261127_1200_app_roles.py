"""Le rôle d'APP d'un partage (`docs/apps.md` § Rôles d'une app) : `resource_grants.app_role`,
et l'accès à un tableau À TRAVERS une app seulement : la table `app_table_access`
(fragment `ACCES_PAR_L_APP`, `CREATE TABLE IF NOT EXISTS` — neuve, vide, sans verrou
sur l'existant hors les FK vers `apps` et `user_datastores`, un `ShareRowExclusiveLock`
bref).

Une colonne NEUVE, nullable, sans défaut — le DDL est le fragment
`db/schema/apps.py::ROLES`, exécuté tel quel (`ADD COLUMN IF NOT EXISTS`). La clé d'un
des rôles que le manifeste d'une app déclare (`manifest.roles`) ; NULL = le rôle par
défaut du manifeste, LU comme tel (rien n'est recopié : l'état de toutes les lignes
d'avant, et de tous les partages qui ne sont pas d'une app).

Et l'IDENTITÉ d'un partage (`resource_grants.share_id`, séquence
`resource_grants_share_id_seq`) : un numéro jamais repris, tiré à la pose par
`db.grant_resource`, gardé tant que le partage vit, neuf quand il est reposé après échéance
ou retrait. `app_table_access.share_id` lie l'accès par l'app au partage sous lequel il a
été ouvert : un partage reposé ne le fait pas revivre. NULL pour les partages d'avant —
numérotés à leur prochaine pose, ou au premier accès par l'app ouvert sous eux ; rien
n'est recopié.

**Verrous.** Un `AccessExclusiveLock` bref sur `resource_grants` (colonnes nullables sans
défaut : catalogue seul, aucune réécriture). `lock_timeout` 5 s : la table est lue à
chaque jugement d'accès ; un échec net, qu'on rejoue, plutôt qu'une attente qui
bloquerait ces lectures derrière elle.

**Ordre avec le code : le nouveau après la révision.** Le démarrage pose les colonnes
avant de servir (le nouveau `grant_resource` écrit `share_id`) ; l'ancien code les ignore
(ses `INSERT … ON CONFLICT` ne les nomment pas : un partage qu'il pose reste sans
identité, numéroté plus tard). Une app d'avant ne déclare aucun rôle : rien ne change
pour elle.

Retour arrière : retire la table (les accès par l'app se perdent : ces lecteurs ne
lisent plus ces tableaux), puis les colonnes (chaque lecteur revient au rôle par défaut
de l'app) et la séquence.

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
    op.execute("ALTER TABLE resource_grants DROP COLUMN IF EXISTS share_id")
    op.execute("DROP SEQUENCE IF EXISTS resource_grants_share_id_seq")
    op.execute("ALTER TABLE resource_grants DROP COLUMN IF EXISTS app_role")
