"""Apps : écrans bâtis sur Tulina, rangés dans un projet (`apps`, `app_revisions`).

Des tables NEUVES, nées entières — le DDL est le fragment `db/schema/apps.py::APPS`,
exécuté tel quel. Une app n'est pas une page de mémoire : elle a sa table, ses
révisions, sa capacité (`me.app`, `oto_apps`, `POST /api/me/apps`).

**Verrous.** `AccessExclusiveLock` sur les tables neuves seules (vides, instantané) ; la
clé étrangère vers `projects` prend un `ShareRowExclusiveLock` bref (les lectures
passent) : `lock_timeout` 5 s, un échec net qu'on rejoue.

**Ordre avec le code : indifférent.** Le démarrage pose les mêmes tables (`CREATE TABLE
IF NOT EXISTS`, même fragment) ; rien ne les lit avant la capacité qui les sert.

Retour arrière : retire les tables — et les apps qu'elles portent.

Révision : 20261025_2100_apps
Précédente : 20261024_1101_default_sharing
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.apps import APPS

revision = "20261025_2100_apps"
down_revision = "20261024_1101_default_sharing"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(APPS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS app_revisions")
    op.execute("DROP TABLE IF EXISTS apps")
