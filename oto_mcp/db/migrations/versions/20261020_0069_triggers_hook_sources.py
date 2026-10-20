"""Les webhooks qu'oto inscrit chez la source d'un agent (docs/runner-et-automatisations.md §Brancher une source).

Une colonne NEUVE, nullable, sans défaut — le DDL est
`db/runner_triggers.py::DDL_COLONNE_HOOK_SOURCES`, exécuté tel quel :
`runner_triggers.hook_sources JSONB`. La liste `[{source, remote_id, event,
campaign_id}]` des webhooks posés chez le tiers par `connect_source` ; NULL = rien
d'inscrit, l'état de tous les agents d'avant.

**Verrous.** `AccessExclusiveLock` bref sur `runner_triggers` (colonne nullable sans
défaut : catalogue seul, aucune réécriture). `lock_timeout` 5 s : le tick lit la table
en continu ; un échec net, qu'on rejoue, plutôt qu'une attente qui bloquerait ses
lectures derrière elle.

**Ordre avec le code : indifférent.** Le code de ce lot lit la colonne (`_COLS`),
mais le démarrage la pose avant de servir (`ADD COLUMN IF NOT EXISTS`, même DDL, sous
garde de catalogue) ; l'ancien code l'ignore.

Retour arrière : retire la colonne. Les webhooks déjà inscrits chez la source y
restent, et oto ne sait plus les retirer : les débrancher AVANT (`disconnect_source`).

Révision : 0069_triggers_hook_sources
Précédente : 0068_credits
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.runner_triggers import COLONNE_HOOK_SOURCES, DDL_COLONNE_HOOK_SOURCES

revision = "0069_triggers_hook_sources"
down_revision = "0068_credits"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(DDL_COLONNE_HOOK_SOURCES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE runner_triggers DROP COLUMN IF EXISTS {COLONNE_HOOK_SOURCES}")
