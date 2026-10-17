"""Plafond d'engagement d'une org sur une clé plateforme, poussé par le commerce.

Une table NEUVE, née entière — le DDL est le fragment
`db/schema/usage.py::ORG_SPEND_CAPS`, exécuté tel quel :
`org_spend_caps (org_id, connector) PK, units_left, as_of, engaged, set_at`.

**Verrous.** `AccessExclusiveLock` sur la table neuve seule (vide, instantané).

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE
IF NOT EXISTS`, même fragment), y compris sur une base existante ; la réservation qui
la lit (`access/spend_cap.py`) est fail-open : sans la table, elle journalise et sert
l'appel. Tant que le commerce n'a rien poussé, aucune ligne : aucun plafond.

Retour arrière : retire la table (le commerce repousse les plafonds à son passage
suivant).

Révision : 0065_org_spend_caps
Précédente : 0064_orgs_trial_ends_at
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.usage import ORG_SPEND_CAPS

revision = "0065_org_spend_caps"
down_revision = "0064_orgs_trial_ends_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(ORG_SPEND_CAPS)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_spend_caps")
