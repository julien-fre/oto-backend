"""emails_cc : un email différé garde ses copies (`email_send(cc=…)`).

`scheduled_emails.cc TEXT[]`, nullable (NULL = aucune copie) : toute ligne existante
garde exactement son envoi.

**Ordre avec le code : AVANT la fusion.** Le code du même lot ÉCRIT la colonne à chaque
mise en file (`enqueue_scheduled_email`) et la LIT au tirage (`claim_due_scheduled_emails`) :
jouée après, chaque envoi différé échouerait en `UndefinedColumn`. L'ancien code
l'ignore. Une base NEUVE l'a déjà (`db/schema/emails.py`).

Posée après `0036_partages_de_procedure` (#1124), au bout de la file 0032 (#1145) →
0033 (#1118) → 0034 (#1115) → 0035 (#1117) → 0036 : elle se joue après elle.

Verrou : `ADD COLUMN` nullable sans défaut = écriture de catalogue, instantanée ;
attente bornée par `lock_timeout` 5 s.

Retour arrière : retire la colonne (les copies des envois encore en file sont perdues).

Révision : 0037_emails_cc
Précédente : 0036_partages_de_procedure
"""
from __future__ import annotations

from alembic import op

revision = "0037_emails_cc"
down_revision = "0036_partages_de_procedure"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE scheduled_emails ADD COLUMN IF NOT EXISTS cc TEXT[]")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE scheduled_emails DROP COLUMN IF EXISTS cc")
