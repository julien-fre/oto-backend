"""Plafond de consommation des abonnements : la table d'org, la colonne perso.

Deux gestes additifs :

1. la table NEUVE `org_model_subscription_limits` — le plafond qu'une org règle sur
   les abonnements de ses membres —, que le démarrage crée aussi (`CREATE TABLE IF NOT
   EXISTS`, fragment `db/schema/runs.py::MODEL_SUBSCRIPTION_LIMITS`, exécuté tel quel
   ici). Sa clé étrangère vers `orgs` prend un verrou sur `orgs` à la création ;
2. `user_model_subscriptions.limite_pct SMALLINT CHECK (1..100)`, NULLABLE, sans
   défaut : le plafond perso. Écriture de catalogue ; la vérification du `CHECK`
   parcourt la table, qui compte une ligne par personne abonnée (quelques-unes). Le
   démarrage pose la même colonne s'il ne la trouve pas
   (`user_subscriptions.DDL_COLONNE_LIMITE`, sous garde de catalogue).

Les `ALTER`/`CREATE` prennent leurs verrous sous `lock_timeout` : un échec net, qu'on
rejoue, plutôt qu'une file de réservations bloquées derrière nous.

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit ni la table ni la
colonne : jouer cette révision avant ou après le déploiement est sûr, et le code du
lot arrive avec le démarrage qui pose les deux (ordre indifférent, comme 0017). La
jouer AVANT la fusion reste le plus sûr : le code du lot LIT la colonne à chaque
lecture d'abonnement, donc à chaque réservation d'un travail sur abonnement. Le
retour arrière retire la colonne puis la table, et ce qu'elles savaient ; le code qui
les lit doit être retiré avant lui.

Révision : 0019_plafond_abonnements
Précédente : 0018_contexte_org_rempli
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.runs import MODEL_SUBSCRIPTION_LIMITS
from oto_mcp.db.user_subscriptions import COLONNE_LIMITE, DDL_COLONNE_LIMITE

revision = "0019_plafond_abonnements"
down_revision = "0018_contexte_org_rempli"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(MODEL_SUBSCRIPTION_LIMITS)
    op.execute(DDL_COLONNE_LIMITE)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE user_model_subscriptions DROP COLUMN IF EXISTS {COLONNE_LIMITE}")
    op.execute("DROP TABLE IF EXISTS org_model_subscription_limits")
