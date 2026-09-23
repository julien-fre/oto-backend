"""billing_contracts : les abonnements réglés HORS PLATEFORME (contrat, virement).

Un admin plateforme déclare un abonnement payé ailleurs : licences, prix pour mémoire,
début, fin facultative (reconduction tacite), référence. La ligne `org_subscriptions`
de l'org porte `provider='contract'` ; ses paramètres vivent dans cette table NEUVE.

Le `CREATE TABLE` vit dans le fragment `db/schema/billing.py` (`CONTRACTS`) et cette
révision l'exécute tel quel. Comme toute table neuve, il est aussi joué au démarrage
(`CREATE TABLE IF NOT EXISTS`, ADR 0065) : révision et boot sont idempotents l'un
envers l'autre, l'ordre entre eux est indifférent. La clé étrangère vers `orgs` prend
un verrou sur `orgs` à la création seulement, d'où le `lock_timeout`.

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ignore la table : jouer cette
révision avant ou après le déploiement est sûr, et un retour au tag précédent aussi.
Le retour arrière (`downgrade`) supprime la table ET ses lignes.

Révision : 0008_billing_contracts
Précédente : 0007_jetons_revocation_tracee
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.billing import CONTRACTS

revision = "0008_billing_contracts"
down_revision = "0007_jetons_revocation_tracee"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(CONTRACTS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS billing_contracts")
