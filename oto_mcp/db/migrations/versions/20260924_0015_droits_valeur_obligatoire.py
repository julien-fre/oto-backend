"""org_entitlements : `value` NOT NULL, et la clé primaire d'avant la portée personne
retirée — oto-backend#1066.

La seconde moitié de `0014_droits_portee_personne`. Deux gestes que l'ancien code ne
supporte pas, d'où leur place à part :

1. `value` NULL → `1` (les lignes posées par l'ancien code depuis la 0014), puis
   `SET NOT NULL` — une valeur vide ne s'écrit plus ;
2. `DROP CONSTRAINT org_entitlements_pkey` : la clé `(org_id, right_key, source)`
   interdirait une ligne de personne à côté de la ligne d'org du même droit et de la
   même source. L'unicité est désormais `org_entitlements_une_ligne` (0014).

**Ordre : APRÈS le tag de production** — plus aucun processus ne doit servir le code
d'avant #1066, qui pose `value` NULL et cible la PK dans son `ON CONFLICT`. Entre la
fusion et cette révision, le nouveau code tourne sur une base qui a encore la PK : une
ligne de PERSONNE du même (org, droit, source) qu'une ligne d'org y serait refusée —
aucun producteur n'en pose encore. Et une ligne à `value` NULL qu'y aurait posée
l'ancien code compte pour « aucune ligne » à la lecture (`MAX` ignore NULL) jusqu'au
premier passage de la réconciliation du nouveau code, au démarrage, qui la repose à 1.

`SET NOT NULL` parcourt la table sous `AccessExclusiveLock` (une poignée de lignes),
borné par `lock_timeout`. Idempotente : sur une base NEUVE, chaque geste est sauté.

Retour arrière : `value` redevient nullable et la PK est reposée — il **échoue de
lui-même** si deux lignes partagent (org_id, right_key, source), c'est-à-dire si une
ligne de personne double une ligne d'org : les retirer d'abord.

Révision : 0015_droits_valeur_obligatoire
Précédente : 0014_droits_portee_personne
"""
from __future__ import annotations

from alembic import op

revision = "0015_droits_valeur_obligatoire"
down_revision = "0014_droits_portee_personne"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("UPDATE org_entitlements SET value = 1 WHERE value IS NULL")
    op.execute("ALTER TABLE org_entitlements ALTER COLUMN value SET NOT NULL")
    op.execute("ALTER TABLE org_entitlements DROP CONSTRAINT IF EXISTS org_entitlements_pkey")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE org_entitlements ALTER COLUMN value DROP NOT NULL")
    op.execute("ALTER TABLE org_entitlements "
               "ADD PRIMARY KEY (org_id, right_key, source)")
