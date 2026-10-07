"""jev_jobs : `jev_rows` en tâche de fond (`jev_rows(background=true)`).

Un appel `jev_rows` jugeait ~100 lignes (sa fenêtre de 45 s) : un tableau de 6 000 lignes
demandait ~60 appels et un agent qui boucle, et une connexion perdue arrêtait tout. Le
travail se dépose ici et la boucle `jev_jobs_worker` le mène tranche par tranche, sous
l'identité de l'appelant.

Une table NEUVE, née entière, deux index partiels : aucune réécriture, aucun parcours
d'une table servie. Le DDL est le fragment `db/schema/jev.py::JEV_JOBS`, exécuté tel quel.

**Ordre avec le code : indifférent.** Le démarrage pose la même table (`CREATE TABLE IF
NOT EXISTS`, même fragment). L'ancien code l'ignore. Prod et préprod partagent la base.

Retour arrière : retire la table — les travaux en cours s'arrêtent, ce qu'ils ont écrit
dans les tableaux reste (et une relance `jev_rows` ne reprend que les lignes non jugées).

Révision : 0044_jev_jobs (⚠️ numéro provisoire : d'autres PR ouvertes en tiennent)
Précédente : 0043_signal_occurrences
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.jev import JEV_JOBS

revision = "0044_jev_jobs"
down_revision = "0043_signal_occurrences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(JEV_JOBS)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS jev_jobs")
