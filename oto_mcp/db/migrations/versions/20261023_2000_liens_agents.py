"""Les liens entre agents (docs/runner-et-automatisations.md §Lier des agents).

Une table NEUVE, née entière, et deux index sur elle — le DDL est le fragment
`db/schema/runs.py::AGENT_LINKS`, exécuté tel quel (une seule écriture) :
`runner_trigger_links (id, org_id, from_trigger_id → runner_triggers ON DELETE CASCADE,
to_trigger_id → runner_triggers ON DELETE CASCADE, kind ∈ {starts, requires},
condition, created_by, created_at)`, unique `(from, to, kind)`.

**Verrous.** `AccessExclusiveLock` sur la table neuve (vide, instantané) ; les deux clés
étrangères prennent un `ShareRowExclusiveLock` BREF sur `runner_triggers` (aucune
réécriture, aucun parcours : la table neuve est vide). `lock_timeout` 5 s : le tick lit
`runner_triggers` en continu ; un échec net, qu'on rejoue, plutôt qu'une attente qui
bloquerait ses lectures derrière elle.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE
IF NOT EXISTS`, même fragment, sous garde de catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la table (les liens posés se perdent ; un run hébergé ne peut
alors plus rien lancer — le code qui exige un lien doit partir avec).

Révision : 20261023_2000_liens_agents
Précédente : 20261023_1040_echec_annonce
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.runs import AGENT_LINKS

revision = "20261023_2000_liens_agents"
down_revision = "20261023_1040_echec_annonce"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(AGENT_LINKS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS runner_trigger_links")
