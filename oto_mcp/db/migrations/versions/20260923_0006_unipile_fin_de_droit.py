"""unipile_accounts : `entitlement_lost_at` et `entitlement_notice_at`, la fin du droit.

oto-backend#806 : quand une org perd le droit `unipile`, ses comptes branchés sur la clé
de la plateforme sont supprimés chez unipile après un délai, le propriétaire prévenu.
Le travail `oto-mcp maintenance unipile-fin-de-droit` date le premier constat de la
perte (`entitlement_lost_at`, d'où part le délai) et l'envoi du préavis
(`entitlement_notice_at`). Deux colonnes NULLABLES, sans défaut : pour PostgreSQL, une
écriture de catalogue seule, sans réécriture ni parcours de la table.

Le démarrage pose les mêmes colonnes (`db/_init.py`, `ALTER … ADD COLUMN IF NOT EXISTS`,
sauté par le garde des DDL quand elles existent) : révision et boot sont idempotents
l'un envers l'autre, et l'ordre entre eux est indifférent. L'`ALTER` prend un
`AccessExclusiveLock` sur `unipile_accounts`, que chaque appel de messagerie lit —
d'où le `lock_timeout` : un échec net, qu'on rejoue, plutôt qu'une file de lecteurs
bloqués derrière nous (incident du 18/09 sur `orgs`, docs/migrations-versionnees.md).

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ne lit ni n'écrit ces colonnes
(ajout pur) : jouer cette révision avant ou après le déploiement est sûr, et un retour
au tag précédent aussi. Le retour arrière (`downgrade`) efface les marques ; le passage
suivant du travail les repose, en repartant du jour même — le délai recommence.

Révision : 0006_unipile_fin_de_droit
Précédente : 0005_journal_archives
"""
from __future__ import annotations

from alembic import op

revision = "0006_unipile_fin_de_droit"
down_revision = "0005_journal_archives"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre la messagerie derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE unipile_accounts "
               "ADD COLUMN IF NOT EXISTS entitlement_lost_at TIMESTAMPTZ, "
               "ADD COLUMN IF NOT EXISTS entitlement_notice_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE unipile_accounts "
               "DROP COLUMN IF EXISTS entitlement_notice_at, "
               "DROP COLUMN IF EXISTS entitlement_lost_at")
