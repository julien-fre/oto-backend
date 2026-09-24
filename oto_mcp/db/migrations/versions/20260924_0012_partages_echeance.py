"""resource_grants : un partage peut porter une échéance (`expires_at`) — otomata-tech/oto#39.

Les jetons d'API en portent une depuis le 04/09 ; les partages de ressource (audience ×
rôle, ADR 0048) n'en avaient pas. Une colonne NULLABLE, sans défaut ni index : NULL =
sans échéance, donc tous les partages existants gardent exactement leur sens. Pour
PostgreSQL, une écriture de catalogue seule, sans réécriture ni parcours.

Mais l'`ALTER` prend un `AccessExclusiveLock` sur `resource_grants`, que CHAQUE contrôle
d'accès à un contenu lit (`ownership.can_access`, la visibilité d'un projet, d'un
tableau, d'une page) : d'où le `lock_timeout` — un échec net, qu'on rejoue, plutôt
qu'une file de requêtes bloquées derrière nous (incident du 18/09 sur `orgs`,
docs/migrations-versionnees.md). Et c'est pourquoi la colonne n'est PAS posée au
démarrage. Une base NEUVE la reçoit du `CREATE TABLE` (`db/schema/grants.py`).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit ni n'écrit la colonne
(ajout pur) : jouer cette révision AVANT le déploiement est sûr. Un retour au tag
précédent l'est aussi, à ceci près qu'un partage échu redeviendrait valide sous l'ancien
code, qui ne connaît pas `expires_at`. L'inverse n'est pas sûr : le code qui l'accompagne
lit `expires_at` à chaque contrôle d'accès et répondrait `UndefinedColumn` — **la
révision s'applique AVANT la fusion** (main = préprod).

Révision : 0012_partages_echeance
Précédente : 0011_journal_revisions_ligne
"""
from __future__ import annotations

from alembic import op

revision = "0012_partages_echeance"
down_revision = "0011_journal_revisions_ligne"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre les contrôles d'accès derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE resource_grants ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE resource_grants DROP COLUMN IF EXISTS expires_at")
