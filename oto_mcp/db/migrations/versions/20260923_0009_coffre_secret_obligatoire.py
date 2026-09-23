"""connector_credentials : `secret_enc` NOT NULL — une ligne du coffre détient un secret.

oto-backend#521 : le schéma disait `secret_enc` « obligatoire » en commentaire et la
laissait nullable. Deux lectures en tiraient deux définitions de « détient une clé » —
`has_credential` filtrait `secret_enc IS NOT NULL`, `list_credentials` non. Aucune
divergence active (le seul INSERT chiffre toujours, aucun UPDATE ne remet à NULL),
mais rien ne l'interdisait. La base l'interdit désormais ; le code du même lot ne lit
plus que la présence de la ligne.

`SET NOT NULL` parcourt la table sous `AccessExclusiveLock` (quelques centaines de
lignes : millisecondes). Chaque résolution de credential la lit — d'où le
`lock_timeout` : un échec net, qu'on rejoue, plutôt qu'une file d'appels bloqués
derrière nous (incident du 18/09 sur `orgs`, docs/migrations-versionnees.md).
**La révision échoue d'elle-même** (`column "secret_enc" … contains null values`) si
une ligne nulle existe : rien n'est écrit, rien n'est masqué. Vérifier avant :

    SELECT count(*) FROM connector_credentials WHERE secret_enc IS NULL;  -- attendu : 0

Une base NEUVE reçoit la contrainte du `CREATE TABLE` (`db/schema/connectors.py`).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code n'écrit jamais NULL : jouer
cette révision avant le déploiement est sûr, un retour au tag précédent aussi. Le code
du même lot ne filtre plus `secret_enc IS NOT NULL` : **la révision s'applique AVANT la
fusion** (main = préprod), pour qu'il ne tourne jamais sur une base qui tolère le nul.

Révision : 0009_coffre_secret_obligatoire
Précédente : 0008_billing_contracts
"""
from __future__ import annotations

from alembic import op

revision = "0009_coffre_secret_obligatoire"
down_revision = "0008_billing_contracts"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre les résolutions de credential.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE connector_credentials ALTER COLUMN secret_enc SET NOT NULL")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE connector_credentials ALTER COLUMN secret_enc DROP NOT NULL")
