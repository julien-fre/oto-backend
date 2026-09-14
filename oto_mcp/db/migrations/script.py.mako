"""${message}

Révision : ${up_revision}
Précédente : ${down_revision | comma,n}
Écrite le : ${create_date}

⚠️ Écrire le SQL à la main (`op.execute(...)`). Pas d'ORM ici, et pas de
détection automatique : ce qui n'est pas écrit ne sera pas appliqué.
Toute migration destructrice se découpe en lots — cf. `docs/live-migrations.md`.
"""
from __future__ import annotations

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
