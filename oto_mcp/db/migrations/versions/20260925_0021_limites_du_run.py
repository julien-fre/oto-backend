"""Les limites d'UN run déclarées par l'utilisateur : trois colonnes nullables.

- `runner_triggers.max_tokens INT` — le plafond de jetons d'un run de l'agent ;
- `runner_triggers.max_run_seconds INT` — sa durée murale ;
- `runner_fleets.max_run_seconds INT` — la durée murale d'un travail (une ligne). Le
  plafond de jetons d'une ligne existait déjà (`max_tokens_per_row`).

Sans défaut : NULL = rien ne part avec le travail, l'exécuteur garde ses limites
(`capabilities/_limites_du_run.py`). Écritures de catalogue seulement, aucun parcours
de lignes. Le démarrage pose les mêmes colonnes s'il ne les trouve pas
(`ALTER … ADD COLUMN IF NOT EXISTS`, `db/_init.py`) : idempotents l'un envers l'autre,
même régime que 0006.

⚠️ Prod et préprod partagent la MÊME base. Le code du lot LIT ces colonnes à chaque
lecture d'un déclencheur ou d'une flotte (`_COLS`) : la jouer **avant la fusion** — le
démarrage de la préproduction les poserait de toute façon, mais la révision évite de
dépendre d'un `ALTER` de boot sur deux tables que le tick lit toutes les minutes.
L'ancien code les ignore. Le retour arrière retire les colonnes et les limites
déclarées ; le code qui les lit doit être retiré avant lui.

Révision : 0021_limites_du_run
Précédente : 0020_pool_abonnements
"""
from __future__ import annotations

from alembic import op

revision = "0021_limites_du_run"
down_revision = "0020_pool_abonnements"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre le tick derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE runner_triggers "
               "ADD COLUMN IF NOT EXISTS max_tokens INT, "
               "ADD COLUMN IF NOT EXISTS max_run_seconds INT")
    op.execute("ALTER TABLE runner_fleets "
               "ADD COLUMN IF NOT EXISTS max_run_seconds INT")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE runner_fleets DROP COLUMN IF EXISTS max_run_seconds")
    op.execute("ALTER TABLE runner_triggers "
               "DROP COLUMN IF EXISTS max_run_seconds, "
               "DROP COLUMN IF EXISTS max_tokens")
