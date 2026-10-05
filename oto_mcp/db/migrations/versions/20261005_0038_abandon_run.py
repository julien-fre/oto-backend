"""abandon_run : une ligne abandonnée garde le run de sa dernière tentative (#491).

`datastore_rows.abandon_run TEXT`, nullable : posé avec `abandon_reason` quand le plafond
de reprises sort une ligne de la file, effacé avec lui à la première écriture. Toute
ligne existante reste NULL — y compris une ligne déjà abandonnée, dont le run n'a été
gardé nulle part.

**Ordre avec le code : AVANT la fusion.** Le code du même lot LIT la colonne dans chaque
projection de ligne (`db/datastore.py`, `db/rowlock.py`) et l'ÉCRIT à l'abandon
(`db/rowabandon.py`) : jouée après, toute lecture de tableau de la préproduction
échouerait en `UndefinedColumn`. L'ancien code l'ignore. Une base NEUVE l'a déjà
(`db/schema/datastore.py`).

Posée après `0037_emails_cc` : elle se joue après elle.

Verrou : `ADD COLUMN` nullable sans défaut = écriture de catalogue, instantanée ;
attente bornée par `lock_timeout` 5 s — `datastore_rows` est la table la plus
sollicitée, une lecture longue en cours fait échouer la révision plutôt que d'empiler
les écritures derrière elle (la rejouer suffit).

Retour arrière : retire la colonne (les runs gardés sur les lignes abandonnées sont
perdus ; les motifs, eux, restent).

Révision : 0038_abandon_run
Précédente : 0037_emails_cc
"""
from __future__ import annotations

from alembic import op

revision = "0038_abandon_run"
down_revision = "0037_emails_cc"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE datastore_rows ADD COLUMN IF NOT EXISTS abandon_run TEXT")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE datastore_rows DROP COLUMN IF EXISTS abandon_run")
