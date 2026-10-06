"""orgs : une suspension dit si elle vient de la désactivation d'un TENANT
(`orgs.suspended_tenant_id`) — oto-backend#1165.

Désactiver un tenant suspend aussi toutes ses orgs (`orgs.tenant_id`), par le mécanisme
de suspension d'org existant (`org_suspension`). Le réactiver doit lever CES
suspensions-là, et elles seules : une org suspendue pour une autre raison (un essai fini
sans abonnement, posé par le commerce) reste suspendue. `suspended_by` (un sub, repointé
par la fusion de comptes) et `suspended_reason` (texte libre) ne le disent pas de façon
sûre ; la colonne le dit : l'id du tenant dont la désactivation a posé la suspension,
NULL pour une suspension posée sur l'org elle-même (`docs/orgs-suspendues.md`).

Une colonne NULLABLE, sans défaut, sans index ni clé étrangère : pour PostgreSQL, une
écriture de catalogue seule, sans réécriture ni parcours. L'`ALTER` prend un
`AccessExclusiveLock` sur `orgs`, lue à presque chaque requête : d'où le `lock_timeout`
— un échec net, qu'on rejoue, plutôt qu'une file de lecteurs derrière nous (incident du
18/09 sur `orgs`, `docs/migrations-versionnees.md`). Le balayage par tenant reste sans
index : `orgs` est une petite table, lue en entier toutes les 30 s par processus.

Une base NEUVE la reçoit du `CREATE TABLE` (`db/schema/orgs.py`).

⚠️ **Ordre avec le code : AVANT la fusion.** Le code du même lot l'écrit à chaque
suspension et levée d'org (commerce, super admin) et à chaque (dés)activation de tenant :
jouée après, ces gestes-là échoueraient en `UndefinedColumn`. Le prédicat servi (la
liste des orgs suspendues, `claim_next_job`) ne la lit pas : aucune requête de membre
n'en dépend. L'ancien code l'ignore (ajout pur) : jouer la révision avant le déploiement
est sûr. Prod et préprod partagent la base.

Retour arrière : retire la colonne — une suspension posée par un tenant devient une
suspension d'org ordinaire, que `op=enable` du tenant ne lève plus (le commerce ou un
super admin la lèvent).

Révision : 0042_orgs_suspension_par_tenant
Précédente : 0041_recherche_valeurs_servies
"""
from __future__ import annotations

from alembic import op

revision = "0042_orgs_suspension_par_tenant"
down_revision = "0041_recherche_valeurs_servies"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS suspended_tenant_id BIGINT")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE orgs DROP COLUMN IF EXISTS suspended_tenant_id")
