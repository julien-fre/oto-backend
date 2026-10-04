"""tool_calls : index partiel (org_id, tool, created_at DESC) WHERE ok — les relevés d'org.

oto-backend#1145 (incident du 04/10/2026 : la base partagée a basculé six fois, la
réserve de connexions prise par quelques routes lourdes appelées en rafale).

Le relevé de consommation (`list_billable_calls_for_org`, route
`GET /api/orgs/{id}/usage/calls`, plus de 290 000 appels en deux jours) filtre
`org_id = ? AND tool = ? AND ok AND created_at` dans une fenêtre. `tool_calls` n'avait
que `idx_tool_calls_org (org_id, created_at DESC)` et `idx_tool_call_log_tool (tool)` :
le parcours lisait TOUS les appels de l'org sur la fenêtre, puis jetait les autres
outils et les échecs. `idx_tool_calls_org_tool_ok` sert l'égalité sur les deux
premières colonnes et l'ordre récent d'abord sur la troisième ; le prédicat `ok`
écarte les échecs, qu'aucune lecture de relevé ne compte (un échec n'a rien consommé).

**Posé CONCURRENTLY**, comme `0002_runner_jobs_index_vivant` : un `CREATE INDEX`
ordinaire bloquerait les écritures de `tool_calls` — CHAQUE appel journalisé — le
temps du parcours complet de la table. CONCURRENTLY est refusé dans une transaction ;
`env.py` en ouvre une par défaut, d'où `op.get_context().autocommit_block()`.

**`IF NOT EXISTS`** : l'index est d'abord joué à la main en production, la révision
ne fait alors que le constater.

**Le verdict vit dans `oto_mcp/db/index_releve.py`**, partagé avec le démarrage :
la révision ne construit que si l'index est ABSENT et `tool_calls` PETITE (base
neuve, vide, préproduction fraîche — au plus `CONSTRUCTION_MAX_LIGNES`, estimées par
`pg_class.reltuples`). Sur une `tool_calls` de production (12 M lignes, 172 s de
construction mesurées), elle lève `ConstructionManuelleRequise` ; si elle trouve
l'index INVALIDE (construction CONCURRENTLY interrompue, que `IF NOT EXISTS` prendrait
pour fait), `IndexInvalide`. Les deux renvoient au geste manuel
(`docs/migrations-versionnees.md` §5.1) au lieu de lancer deux parcours de la table
dans la fenêtre d'une migration. Après sa propre construction, elle vérifie la
validité et lève `IndexInvalide` si l'index n'est pas valide.

**Verrous.** `ShareUpdateExclusiveLock` sur `tool_calls` seulement : ni lectures ni
écritures bloquées, seulement un autre DDL ou un VACUUM. La phase concurrente ATTEND
la fin de toute transaction ouverte avant elle, par des attentes de verrou sur leur
`virtualxid` — une transaction laissée `idle in transaction` la retient d'autant.
`lock_timeout` coupe CES attentes aussi : à 2 s, la construction a échoué en
production (`LockNotAvailable`, index laissé invalide). Il vaut donc 5 min, borne
d'une attente pendant laquelle rien d'autre n'est bloqué. Durée de construction :
deux parcours de la table, proportionnels à sa taille (millions de lignes en
production).

⚠️ Prod et préprod partagent la MÊME base : l'index existe pour les deux dès qu'il
est posé. Une base NEUVE le reçoit du démarrage (`_init.py`, après
`idx_tool_calls_org`) et naît estampillée à la tête du registre.

Retour arrière : `DROP INDEX CONCURRENTLY IF EXISTS idx_tool_calls_org_tool_ok`.

Révision : 0032_tool_calls_org_outil_ok
Précédente : 0031_selection_org_reelle
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db import index_releve

revision = "0032_tool_calls_org_outil_ok"
down_revision = "0031_selection_org_reelle"
branch_labels = None
depends_on = None

#: Hors transaction, un `SET` vaut pour la SESSION : il est remis à zéro en sortie.
_ATTENTE_MAX = "SET lock_timeout = '5min'"
_ATTENTE_RENDUE = "RESET lock_timeout"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()

        def scalaire(sql: str):
            return bind.exec_driver_sql(sql).scalar()

        if not index_releve.a_construire(scalaire):
            return
        op.execute(_ATTENTE_MAX)
        try:
            op.execute(index_releve.DDL_CONCURRENT)
        finally:
            op.execute(_ATTENTE_RENDUE)
        if scalaire(index_releve.SQL_VALIDITE) is not True:
            raise index_releve.IndexInvalide()


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_ATTENTE_MAX)
        try:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_releve.INDEX}")
        finally:
            op.execute(_ATTENTE_RENDUE)
