"""Les messages postés dans Slack par un agent hébergé (docs/runner-et-automatisations.md
§Une réponse dans le fil d'un agent).

Une table NEUVE, née entière, et un index — le DDL est le fragment
`db/schema/runs.py::SLACK_AGENT_POSTS`, exécuté tel quel : `slack_agent_posts (channel,
ts, org_id, trigger_id → runner_triggers ON DELETE CASCADE, job_id, text, created_at)`,
clé `(channel, ts)`, index de purge sur `created_at`.

**Verrous.** `AccessExclusiveLock` sur la table neuve (vide, instantané) ; la clé
étrangère prend un `ShareRowExclusiveLock` BREF sur `runner_triggers` (aucune réécriture,
aucun parcours : la table neuve est vide). `lock_timeout` 5 s : le tick lit
`runner_triggers` en continu ; un échec net, qu'on rejoue, plutôt qu'une attente qui
bloquerait ses lectures derrière elle.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE IF
NOT EXISTS`, même fragment) ; l'ancien code l'ignore.

Retour arrière : retire la table (une réponse dans le fil d'un message déjà posté
n'atteint plus son agent ; le message, lui, reste dans Slack).

Révision : 20261029_1900_slack_agent_posts
Précédente : 20261029_1705_provider_credits
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.runs import SLACK_AGENT_POSTS

revision = "20261029_1900_slack_agent_posts"
down_revision = "20261029_1705_provider_credits"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(SLACK_AGENT_POSTS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS slack_agent_posts")
