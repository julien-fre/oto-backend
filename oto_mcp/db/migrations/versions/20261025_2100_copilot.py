"""L'agent intégré à l'application : ses conversations et ses annulations (docs/copilot.md).

Deux tables NEUVES, nées entières, et un index — le DDL est le fragment
`db/schema/copilot.py::COPILOT`, exécuté tel quel (une seule écriture) :
`copilot_threads (id, sub, org_id, project_id, run_id, system, messages, queue, results,
pending, remembered, client_tools, mode, model_calls, busy_token, busy_until, created_at,
last_seen)` et `copilot_undos (id, thread_id → copilot_threads ON DELETE CASCADE, sub,
label, kind, payload, created_at, expires_at, used_at)`.

**Verrous.** `AccessExclusiveLock` sur les deux tables neuves (vides, instantané) ; la clé
étrangère ne vise qu'une table neuve. Aucune table existante n'est touchée.
`lock_timeout` 5 s, comme les autres révisions.

**Ordre avec le code : indifférent.** Le démarrage pose les mêmes tables (`CREATE TABLE
IF NOT EXISTS`, même fragment) ; l'ancien code les ignore.

Retour arrière : retire les deux tables (les conversations en cours et les annulations
offertes se perdent ; le journal des appels, lui, reste entier).

Révision : 20261025_2100_copilot
Précédente : 20261024_1101_default_sharing
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.copilot import COPILOT

revision = "20261025_2100_copilot"
down_revision = "20261024_1101_default_sharing"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(COPILOT)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS copilot_undos")
    op.execute("DROP TABLE IF EXISTS copilot_threads")
