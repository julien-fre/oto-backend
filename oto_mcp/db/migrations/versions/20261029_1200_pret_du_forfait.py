"""Le prêt du forfait d'un agent, et l'auteur de sa dernière retouche
(docs/runner-et-automatisations.md §Un agent est à son propriétaire).

Deux colonnes NEUVES sur `runner_triggers`, nullables, sans défaut — les mêmes DDL que le
démarrage (`db/_init.py`, en littéral) :

- `forfait_prete_model TEXT` : le modèle sous lequel le propriétaire a prêté son
  abonnement personnel aux équipes et à l'org entière qu'il a partagées en écriture
  (`oto_trigger op=lend`). NULL = aucun prêt collectif, l'état de TOUS les agents
  d'avant : la révision ne remplit rien, rien n'est rétroactif. Le prêt NOMMÉ (partage
  à une personne posé par le propriétaire) n'en dépend pas et reste tel quel.
- `updated_by TEXT` : l'auteur de la dernière retouche (`update`). NULL = non enregistré
  (agent d'avant). Colonne-compte, repointée par la fusion (`db/users._SUB_COLUMNS`).

**Verrous.** `AccessExclusiveLock` bref sur `runner_triggers` (colonnes nullables sans
défaut : catalogue seul, aucune réécriture). `lock_timeout` 5 s : le tick lit la table en
continu ; un échec net, qu'on rejoue, plutôt qu'une attente qui bloquerait ses lectures.

**Ordre avec le code : indifférent.** Le démarrage pose les deux colonnes avant de servir
(`ADD COLUMN IF NOT EXISTS`, sous garde de catalogue) ; l'ancien code les ignore.

Retour arrière : retire les deux colonnes (les prêts collectifs et les auteurs se perdent ;
les éditeurs nommés gardent leur prêt).

Révision : 20261029_1200_pret_du_forfait
Précédente : 20261028_1900_access_requests
"""
from __future__ import annotations

from alembic import op

revision = "20261029_1200_pret_du_forfait"
down_revision = "20261028_1900_access_requests"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE runner_triggers ADD COLUMN IF NOT EXISTS updated_by TEXT")
    op.execute("ALTER TABLE runner_triggers ADD COLUMN IF NOT EXISTS forfait_prete_model TEXT")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE runner_triggers DROP COLUMN IF EXISTS forfait_prete_model")
    op.execute("ALTER TABLE runner_triggers DROP COLUMN IF EXISTS updated_by")
