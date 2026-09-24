"""org_entitlements : la portée personne et l'unicité qui la tient — oto-backend#1066.

ADR 0070 §7 : un droit déclaré vaut pour une org OU pour une personne dans l'org. Trois
gestes, tous ADDITIFS pour le code en place :

1. `sub TEXT` nullable (NULL = ligne d'org, le cas de toutes les lignes existantes) ;
2. `value` NULL → `1` : une valeur vide posée par l'ancienne réconciliation voulait dire
   « oui » (droits oui/non : `unipile`, `platform_unmetered`) ;
3. la contrainte `org_entitlements_une_ligne`, `UNIQUE NULLS NOT DISTINCT (org_id, sub,
   right_key, source)` — la cible de l'`ON CONFLICT` du nouveau code.

**Ce qu'elle ne fait PAS, et pourquoi** : elle garde la clé primaire `(org_id,
right_key, source)` et laisse `value` nullable. L'ancien code — celui que la production
sert jusqu'à son tag — fait `ON CONFLICT (org_id, right_key, source)` (il lui faut
cette PK) et pose encore `value` NULL pour un droit oui/non (il lui faut la colonne
nullable). Retirer l'une ou l'autre ici ferait échouer la réconciliation des droits en
production, y compris APRÈS l'activation d'un abonnement. Ces deux retraits sont la
révision suivante, `0015_droits_valeur_obligatoire`, jouée APRÈS le tag de production.

**Ordre : AVANT la fusion** (main = préprod). Le code du même lot LIT et ÉCRIT `sub`, et
cible la nouvelle contrainte : sans elle, la préproduction répondrait `UndefinedColumn`
à chaque lecture de droit. L'ancien code ignore `sub` et la contrainte (ses lignes ont
`sub` NULL : elle ne refuse rien que la PK n'eût déjà refusé).

Idempotente : une base NEUVE a déjà la forme cible (fragment `db/schema/entitlements.py`)
— chaque geste y est sauté. Verrou : `ADD CONSTRAINT UNIQUE` construit un index sous
`ShareLock` (une poignée de lignes), borné par `lock_timeout`.

Vérifier avant (information, rien ne bloque) :

    SELECT count(*) FROM org_entitlements WHERE value IS NULL;

Retour arrière : retire la contrainte et la colonne — les lignes de PERSONNE sont
supprimées d'abord (sans `sub`, elles deviendraient des lignes d'org). Les `value`
passées à 1 le restent (l'ancien code lit la présence, pas la valeur).

Révision : 0014_droits_portee_personne
Précédente : 0013_pages_versions_regroupees
"""
from __future__ import annotations

from alembic import op

revision = "0014_droits_portee_personne"
down_revision = "0013_pages_versions_regroupees"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre les lecteurs de droits.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE org_entitlements ADD COLUMN IF NOT EXISTS sub TEXT")
    op.execute("UPDATE org_entitlements SET value = 1 WHERE value IS NULL")
    op.execute("""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'org_entitlements_une_ligne') THEN
        ALTER TABLE org_entitlements ADD CONSTRAINT org_entitlements_une_ligne
            UNIQUE NULLS NOT DISTINCT (org_id, sub, right_key, source);
    END IF;
END $$""")


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DELETE FROM org_entitlements WHERE sub IS NOT NULL")
    op.execute("ALTER TABLE org_entitlements "
               "DROP CONSTRAINT IF EXISTS org_entitlements_une_ligne")
    op.execute("ALTER TABLE org_entitlements DROP COLUMN IF EXISTS sub")
