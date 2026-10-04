"""Index partiels de l'usage d'une procédure : chargements et déroulés.

`GET /api/me/instructions/{slug}/usage` (la page d'une procédure) comptait les
chargements (`oto_procedure`) et les déroulés (`run_start`) en quatre lectures du
journal, dont deux SANS borne de date, filtrées par une clé d'`args` qu'aucun index
ne portait : chaque affichage parcourait tous les appels de ces verbes, ou le journal
entier des membres de l'org. Sur une org active, la réponse dépassait la dizaine de
secondes et la page restait en chargement.

La lecture tient désormais en une requête par verbe, bornée à 30 jours, et ces deux
index la servent — même forme qu'`idx_tool_calls_run_finish_ref` (index d'expression
partiel : les seules lignes du verbe, clé = la procédure nommée, puis la date) :

  · `idx_tool_calls_procedure_ref` — `(args->>'slug', created_at DESC)`
    WHERE tool = 'oto_procedure' ;
  · `idx_tool_calls_run_start_ref` — `(args->>…, created_at DESC)` WHERE
    tool = 'run_start', sous la clé que lit déjà `_runs_from_journal`.

**CONCURRENTLY** : un `CREATE INDEX` ordinaire bloquerait les écritures du journal —
c'est-à-dire chaque appel d'outil de la plateforme — le temps du parcours ; refusé
dans un bloc transactionnel, d'où `autocommit_block()` (même régime que 0002, 0030).
Un échec en cours de construction laisse un index INVALIDE : le retirer
(`DROP INDEX CONCURRENTLY`) avant de rejouer, `IF NOT EXISTS` le prendrait pour posé.

Le démarrage pose les mêmes index s'il ne les trouve pas (`usage.DDL_INDEX_USAGE`) :
c'est le chemin d'une base NEUVE, estampillée à la tête. Sur une base peuplée, l'index
de démarrage n'est PAS concurrent : jouer cette révision **avant la fusion**.

⚠️ Prod et préprod partagent la MÊME base. Aucun code ne dépend de la présence des
index (ils accélèrent, ils ne changent aucun résultat) : la révision se joue avant ou
après le déploiement sans risque, et son retour arrière ne casse rien.

Révision : 0032_index_usage_procedure
Précédente : 0031_selection_org_reelle
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.usage import DDL_INDEX_USAGE_CONCURRENT

revision = "0032_index_usage_procedure"
down_revision = "0031_selection_org_reelle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for ddl in DDL_INDEX_USAGE_CONCURRENT.values():
            op.execute(ddl)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for nom in DDL_INDEX_USAGE_CONCURRENT:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {nom}")
