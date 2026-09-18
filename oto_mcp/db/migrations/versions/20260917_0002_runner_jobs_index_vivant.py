"""runner_jobs : index partiel sur les travaux VIVANTS (pending/claimed).

`claim_next_job` (oto_mcp/db/runner_jobs.py) parcourait toute la table à chaque
sondage : mesuré en production le 17/09/2026, `Seq Scan on runner_jobs`, 72 425
lignes écartées (72 284 `done`), 83 231 pages, 152 ms, pour ZÉRO résultat.
`idx_runner_jobs_claim` (posé sur `status = 'pending'` seul) ne couvrait pas
`claimed` — la moitié de la condition de réservation — donc le planificateur ne
pouvait pas s'y limiter et retombait sur un parcours complet.

`idx_runner_jobs_live` couvre les deux statuts vivants (`_schema.py`, même DDL
pour une installation neuve — table et index y naissent ensemble, donc sans
CONCURRENTLY). Ici, sur une base EXISTANTE et déjà peuplée (72k lignes), la
construction doit être CONCURRENTLY : un `CREATE INDEX` ordinaire prend un verrou
qui bloque les écritures de `runner_jobs` le temps du scan complet de la table —
inacceptable sur la base de production partagée (`docs/live-migrations.md`).

CONCURRENTLY est REFUSÉ dans un bloc transactionnel (« cannot run inside a
transaction block ») — `env.py` enveloppe toute migration dans une transaction
par défaut (`context.begin_transaction()`), d'où `op.get_context().autocommit_block()`
qui la referme le temps de ce bloc, exactement comme `_connect_autocommit`
(oto_mcp/db/_conn.py) le fait pour le DDL à chaud applicatif.

`idx_runner_jobs_claim` devient redondant : son prédicat (`status = 'pending'`)
est un sous-ensemble strict de celui du nouvel index. On le dépose, CONCURRENTLY
aussi — un DROP ordinaire prend `AccessExclusiveLock`, `DROP INDEX CONCURRENTLY`
`ShareUpdateExclusiveLock` seulement (ne bloque pas les lectures/écritures).

⚠️ Prod et préprod partagent la MÊME base (`docs/live-migrations.md`) : l'index
sera construit dès le déploiement en préprod, avant toute promotion.

Révision : 0002_runner_jobs_index_vivant
Précédente : 0001_point_de_depart
"""
from __future__ import annotations

from alembic import op

revision = "0002_runner_jobs_index_vivant"
down_revision = "0001_point_de_depart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_runner_jobs_live "
            "ON runner_jobs (org_id, due_at) WHERE status IN ('pending', 'claimed')"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_runner_jobs_claim")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_runner_jobs_claim "
            "ON runner_jobs (org_id, due_at) WHERE status = 'pending'"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_runner_jobs_live")
