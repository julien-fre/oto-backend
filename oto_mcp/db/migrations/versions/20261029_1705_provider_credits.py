"""Les crédits déduits par le fournisseur d'un appel, à côté de sa quantité
(docs/monitoring.md §Ce que le journal facture : `quantity` et `provider_credits`).

Deux colonnes NEUVES, nullables, sans défaut — les DDL sont
`db/journal_calls.py::DDL_COLONNE_CREDITS_FOURNISSEUR` et
`db/journal_jour.py::DDL_COLONNE_CREDITS_FOURNISSEUR`, exécutés tels quels :

- `tool_calls.provider_credits INTEGER` : ce que le fournisseur a déduit pour l'appel,
  dans SON unité. NULL = non tracé (ni 1 ni 0), l'état de toutes les lignes d'avant ;
  `quantity` garde son sens (les items traités) ;
- `journal_totaux_jour.credits_fournisseur BIGINT` : sa somme par groupe, NULL ignoré.
  Les jours déjà consolidés la portent à NULL — rien n'y était tracé.

**Verrous.** `AccessExclusiveLock` bref sur `tool_calls` puis sur `journal_totaux_jour`
(colonnes nullables sans défaut : catalogue seul, aucune réécriture, quelle que soit la
taille du journal). `lock_timeout` 5 s : chaque appel servi écrit dans `tool_calls` ; un
échec net, qu'on rejoue, plutôt qu'une attente qui bloquerait les écritures derrière elle.

**Ordre avec le code : indifférent.** Le démarrage pose les deux colonnes avant de servir
(`ADD COLUMN IF NOT EXISTS`, mêmes DDL, sous garde de catalogue) ; l'ancien code les
ignore. ⚠️ Entre la préproduction et le tag, la maintenance `journal-jour` (production
seule) tourne l'ANCIEN code : un jour qu'elle consolide porte `credits_fournisseur` à NULL
même si la préproduction a déjà tracé des crédits ce jour-là. Après le tag, reconsolider
ces jours (`scripts/rattraper_journal_jour.py --du … --au … --refaire --appliquer`).

Retour arrière : retire les deux colonnes (les crédits tracés se perdent ; `quantity` est
intacte).

Révision : 20261029_1705_provider_credits
Précédente : 20261029_1700_copilot_pieces
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db import journal_calls, journal_jour

revision = "20261029_1705_provider_credits"
down_revision = "20261029_1700_copilot_pieces"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(journal_calls.DDL_COLONNE_CREDITS_FOURNISSEUR)
    op.execute(journal_jour.DDL_COLONNE_CREDITS_FOURNISSEUR)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(f"ALTER TABLE journal_totaux_jour DROP COLUMN IF EXISTS "
               f"{journal_jour.COLONNE_CREDITS_FOURNISSEUR}")
    op.execute(f"ALTER TABLE tool_calls DROP COLUMN IF EXISTS "
               f"{journal_calls.COLONNE_CREDITS_FOURNISSEUR}")
