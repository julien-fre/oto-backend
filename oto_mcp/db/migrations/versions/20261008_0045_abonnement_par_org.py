"""abonnement_par_org : un abonnement personnel ne sert que les orgs où il est ouvert.

Une connexion Claude (la session, le bac à sable) reste une par personne, mais ce
qu'elle sert se décide désormais par org (`user_model_subscription_orgs`) : se
connecter dans une org l'y ouvre, une autre org ne la voit pas tant que la personne
ne l'y a pas ouverte aussi. La pose refuse (`subscription_not_used_here`) et la
réservation fait ATTENDRE les travaux d'une org où l'abonnement n'est pas ouvert.

**Reprise de l'existant — « garder où il sert »** : chaque abonnement existant est
ouvert dans les orgs où il sert DÉJÀ, pour que rien de ce qui tourne ne s'arrête :

- les orgs où la personne possède un agent posé sur un modèle de la famille
  (`runner_triggers.model` en `sub:…`, allumé ou non) ;
- les orgs où elle a des travaux en file sur cette famille ;
- les orgs au pool desquelles elle prête déjà.

Toujours membre de l'org, et un abonnement qui existe : sinon rien n'est ouvert.

**Ordre avec le code : cette révision AVANT le tag.** Le démarrage pose la même table
(vide) par son DDL additif ; un code déployé avant la révision ne trouverait aucune
org ouverte, et les travaux d'abonnement ATTENDRAIENT (ils n'échouent pas) jusqu'à
la révision. L'ancien code ignore la table : la révision seule ne change rien.

Retour arrière : retire la table — l'ancien code sert partout, comme avant.

Révision : 0045_abonnement_par_org (⚠️ numéro provisoire : d'autres PR ouvertes en tiennent)
Précédente : 0044_jev_jobs
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.runs import MODEL_SUBSCRIPTION_ORGS

revision = "0045_abonnement_par_org"
down_revision = "0044_jev_jobs"
branch_labels = None
depends_on = None

# La famille servie par abonnement aujourd'hui, et le préfixe de ses modèles
# (`runner_models` : `sub:sonnet`, `sub:opus`, `sub:haiku`). Écrits en clair : une
# révision ne lit pas le code vivant, qui changera.
_FAMILLE = "claude_subscription"

_REPRISE = f"""
INSERT INTO user_model_subscription_orgs (sub, famille, org_id)
SELECT DISTINCT src.sub, '{_FAMILLE}', src.org_id
  FROM (
        SELECT t.sub, t.org_id FROM runner_triggers t
         WHERE t.model LIKE 'sub:%'
        UNION
        SELECT j.sub, j.org_id FROM runner_jobs j
         WHERE j.status = 'pending' AND j.org_id IS NOT NULL
           AND j.payload->>'model_family' = '{_FAMILLE}'
        UNION
        SELECT l.sub, l.org_id FROM user_model_subscription_loans l
         WHERE l.famille = '{_FAMILLE}'
       ) src
  JOIN user_model_subscriptions ab ON ab.sub = src.sub AND ab.famille = '{_FAMILLE}'
  JOIN org_members om ON om.org_id = src.org_id AND om.sub = src.sub
ON CONFLICT DO NOTHING
"""


def upgrade() -> None:
    op.execute(MODEL_SUBSCRIPTION_ORGS)
    op.execute(_REPRISE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_model_subscription_orgs")
