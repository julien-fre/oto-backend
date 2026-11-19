"""Limites d'envoi LinkedIn posées par le titulaire du compte, et le compte de ce qu'il a
fait sur une fenêtre glissante.

Deux tables NEUVES, nées entières : `unipile_sending_limits` et `unipile_sending_log`
(`db/schema/unipile.py::SENDING_LIMITS`) — posées aussi par le démarrage (`CREATE TABLE
IF NOT EXISTS` de l'assemblage), même DDL. FK vers `users` (ON DELETE CASCADE). Aucune
donnée reprise : sans ligne, aucun compte n'a de limite, rien ne change.

**Verrous.** Tables neuves et vides ; leurs FK prennent un `ShareRowExclusiveLock` bref
sur `users`. `lock_timeout` 5 s : un échec net, qu'on rejoue, plutôt qu'une attente.

**Ordre avec le code : indifférent.** Le démarrage pose les mêmes tables ; l'ancien code
les ignore.

Retour arrière : retire les deux tables. Les limites posées sont perdues ; les envois
repartent sans autre borne que celle de LinkedIn.

Révision : 20261119_1200_sending_limits
Précédente : 20261118_1500_unipile_slots
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.unipile import SENDING_LIMITS

revision = "20261119_1200_sending_limits"
down_revision = "20261118_1500_unipile_slots"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(SENDING_LIMITS)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS unipile_sending_log")
    op.execute("DROP TABLE IF EXISTS unipile_sending_limits")
