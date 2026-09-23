"""org_entitlements : les droits DÉCLARÉS par org, datés, relus à chaque usage.

ADR 0070 §7 : le cœur porte des droits déclarés par org et ne sait pas qui paie. Un
producteur (commerce, admin, partenaire) pose une ligne sous son étiquette `source` ;
le cœur la relit (`db/entitlements.py`, `access/entitlements.py`). Lot d'AJOUT PUR :
rien ne lit encore la table.

Le `CREATE TABLE` vit dans le fragment `db/schema/entitlements.py` et cette révision
l'exécute tel quel — une seule écriture du DDL. Comme toute table neuve, il est aussi
joué au démarrage (`CREATE TABLE IF NOT EXISTS`, ADR 0065) : révision et boot sont
idempotents l'un envers l'autre, et l'ordre entre eux est indifférent.

La clé étrangère vers `orgs` prend un verrou `ShareRowExclusiveLock` sur `orgs` à la
création (et à elle seule : `IF NOT EXISTS` sort avant, table présente) — `orgs` est
lue par chaque requête, d'où le `lock_timeout` : un échec net, qu'on rejoue, plutôt
qu'une file de lecteurs bloqués derrière nous (incident du 18/09 sur `orgs`,
docs/migrations-versionnees.md).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ignore la table : jouer cette
révision avant ou après le déploiement est sûr, et un retour au tag précédent aussi.
Le retour arrière (`downgrade`) supprime la table ET ses lignes ; tant que le fragment
reste dans l'assemblage, le démarrage suivant la recrée vide.

Révision : 0004_org_entitlements
Précédente : 0003_runner_fleets_preneur
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.entitlements import ORG_ENTITLEMENTS

revision = "0004_org_entitlements"
down_revision = "0003_runner_fleets_preneur"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre les lecteurs d'`orgs` derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(ORG_ENTITLEMENTS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS org_entitlements")
