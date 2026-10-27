"""Les épisodes de panne d'un agent hébergé (docs/runner-et-automatisations.md §Soigner
un agent avant qu'on le remarque).

Une table NEUVE, née entière, et deux index — le DDL est le fragment
`db/schema/runs.py::AGENT_EPISODES`, exécuté tel quel : `agent_episodes (id, org_id,
trigger_id, classe, cote, statut, motif, dernier_job_id, travaux_morts, reparations,
diagnostic, ouvert_at, vu_at, clos_at, resolution)`, un index unique partiel (un seul
épisode vivant par agent) et un index de lecture par statut.

**Verrous.** `AccessExclusiveLock` sur la table neuve (vide, instantané). Aucune table
existante n'est touchée, aucune clé étrangère. `lock_timeout` 5 s, comme les autres
révisions.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE IF
NOT EXISTS`, même fragment) ; l'ancien code l'ignore.

Retour arrière : retire la table (l'historique des pannes et des réparations se perd ;
les travaux, eux, restent entiers dans `runner_jobs`).

Révision : 20261027_1300_agent_episodes
Précédente : 20261026_1337_copilot
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.runs import AGENT_EPISODES

revision = "20261027_1300_agent_episodes"
down_revision = "20261026_1337_copilot"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(AGENT_EPISODES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS agent_episodes")
