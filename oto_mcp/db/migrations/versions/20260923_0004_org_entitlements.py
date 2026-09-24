"""org_entitlements : les droits DÉCLARÉS par org, datés, relus à chaque usage.

ADR 0070 §7 : le cœur porte des droits déclarés par org et ne sait pas qui paie. Un
producteur (commerce, admin, partenaire) pose une ligne sous son étiquette `source` ;
le cœur la relit (`db/entitlements.py`, `access/entitlements.py`). Lot d'AJOUT PUR :
rien ne lit encore la table.

Le `CREATE TABLE` est celui du fragment `db/schema/entitlements.py` À LA DATE DE CETTE
révision, recopié ici : le fragment porte depuis la forme cible d'une base neuve
(portée personne, `value` NOT NULL, #1066), que les révisions 0014 et 0015 donnent à
une base existante. Exécuter le fragment courant ici ferait sauter à la 0004 deux
révisions d'histoire. Comme toute table neuve, il est aussi joué au démarrage (`CREATE
TABLE IF NOT EXISTS`, ADR 0065) : révision et boot sont idempotents l'un envers
l'autre.

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


revision = "0004_org_entitlements"
down_revision = "0003_runner_fleets_preneur"
branch_labels = None
depends_on = None

# Le fragment `db/schema/entitlements.py` tel qu'à cette révision (d130f71f).
ORG_ENTITLEMENTS = """
CREATE TABLE IF NOT EXISTS org_entitlements (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    right_key TEXT NOT NULL,
    value INTEGER,
    source TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    granted_by TEXT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, right_key, source)
);
"""

# Au-delà, on abandonne plutôt que de faire attendre les lecteurs d'`orgs` derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(ORG_ENTITLEMENTS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS org_entitlements")
