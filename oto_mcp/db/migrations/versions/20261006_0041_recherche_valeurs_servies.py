"""recherche_valeurs_servies : la fonction et les deux index de `q_scope=values`.

oto-backend#307. La recherche `q` des lignes d'un tableau cherche chaque mot par le
sens (tsquery `french`) OU par fragment (sous-chaîne), dans l'un de deux textes :
`all` = `data::text`, déjà servi par `idx_datastore_rows_fts`/`_trgm` (#67) ; `values`
= les seules valeurs servies, `datastore_valeurs_texte_v1(data)`. Cette révision pose
ce qui sert le second :

1. la fonction `datastore_valeurs_texte_v1(jsonb)` (`db/paths.py`), IMMUTABLE — la
   seule forme indexable d'un texte qui exige de dérouler les cases. Posée seulement
   si elle MANQUE : son corps est figé, deux index en dépendent ;
2. `idx_datastore_rows_valeurs_fts` (GIN, `to_tsvector('french', …)`) et
   `idx_datastore_rows_valeurs_trgm` (GIN trigramme), **CONCURRENTLY IF NOT EXISTS**,
   sous le verdict commun (`db/index_concurrent.py`, celui de 0032) : construits ici
   seulement si `datastore_rows` est PETITE (au plus 10 000 lignes estimées) ; au-delà
   `ConstructionManuelleRequise`, index invalide trouvé `IndexInvalide` — les deux
   donnent le geste manuel (§5.1 de `docs/migrations-versionnees.md`), après lequel
   la révision se rejoue et ne fait que constater. La fonction, elle, est validée
   AVANT le verdict : la révision qui lève l'a déjà posée.

**Ordre avec le code : AVANT la fusion.** Le démarrage du code du lot pose la fonction
s'il ne la trouve pas (écriture de catalogue, aucun verrou de table) — l'ordre est donc
sans danger pour la justesse. Mais il pose aussi les deux index, NON concurrents, sur
une table sous le seuil : jouée avant, la révision (ou le geste manuel) ne lui laisse
rien à construire. Sans les index, `q_scope=values` répond juste, en parcourant les
lignes du tableau visé (`ns_id`).

**Verrous.** `CREATE FUNCTION` : catalogue seul. Les index : `ShareUpdateExclusiveLock`
sur `datastore_rows`, ni lectures ni écritures bloquées ; `lock_timeout` 5 min pour
l'attente des transactions plus anciennes (cf. 0032). Durée : deux parcours de la table,
un appel de la fonction par ligne.

Retour arrière : `DROP INDEX CONCURRENTLY` des deux index, puis `DROP FUNCTION` — à
faire APRÈS le retrait du code qui l'appelle (sinon `q_scope=values` lève
`UndefinedFunction` jusqu'au démarrage suivant, qui la repose).

Révision : 0041_recherche_valeurs_servies
Précédente : 0040_tenants_desactivation
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db import index_concurrent
from oto_mcp.db.paths import (DDL_FONCTION_VALEURS_TEXTE, FONCTION_VALEURS_TEXTE,
                              SQL_FONCTION_VALEURS_PRESENTE)
from oto_mcp.db.search import INDEX_VALEURS

revision = "0041_recherche_valeurs_servies"
down_revision = "0040_tenants_desactivation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # La fonction dans son propre bloc validé : elle reste posée même si le verdict
    # d'un index lève ensuite (table trop grosse) — le geste manuel n'a plus alors
    # que les deux index à construire, et la révision rejouée les constate.
    with op.get_context().autocommit_block():
        if not op.get_bind().exec_driver_sql(SQL_FONCTION_VALEURS_PRESENTE).scalar():
            op.execute(DDL_FONCTION_VALEURS_TEXTE)
    for index in INDEX_VALEURS:
        index_concurrent.poser_par_revision(op, index)


def downgrade() -> None:
    for index in INDEX_VALEURS:
        index_concurrent.retirer_par_revision(op, index)
    op.execute(f"DROP FUNCTION IF EXISTS {FONCTION_VALEURS_TEXTE}(jsonb)")
