"""usage_signal_occurrences : un signal d'usage répété se RATTACHE au signal en attente.

Un signal de même org, même type et même cible, déposé alors qu'un signal de cette clé
attend un arbitrage (open | acknowledged), n'en crée plus un nouveau : il devient une
occurrence de celui-ci (`db/usage.py::insert_usage_signal`). Mesuré le 06/10/2026 : 338
signaux en attente, dont une quarantaine de redites. Chaque occurrence garde son auteur,
sa session, son genre et son texte : rien n'est perdu, seule la pile cesse de compter
des répétitions comme des sujets.

Une table NEUVE, née entière, avec une clé étrangère vers `usage_signals` (une petite
table) et un index : aucune réécriture, aucun parcours d'une table servie. Le DDL est le
fragment `db/schema/usage.py::SIGNAL_OCCURRENCES`, exécuté tel quel — une seule écriture.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE IF
NOT EXISTS`, même fragment), y compris sur une base existante : la couleur neuve la crée
avant de servir. L'ancien code l'ignore. Prod et préprod partagent la base.

Retour arrière : retire la table — les occurrences rattachées sont perdues, leurs
signaux restent (la première occurrence de chacun est la ligne de `usage_signals`).

Révision : 0043_signal_occurrences
Précédente : 0042_orgs_suspension_par_tenant
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.usage import SIGNAL_OCCURRENCES

revision = "0043_signal_occurrences"
down_revision = "0042_orgs_suspension_par_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(SIGNAL_OCCURRENCES)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS usage_signal_occurrences")
