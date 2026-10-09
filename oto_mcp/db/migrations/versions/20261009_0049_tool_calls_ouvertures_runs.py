"""tool_calls : trois index partiels des OUVERTURES de runs — le choix de page des listes.

otomata-tech/infra#9 (« plus de route lourde », décision du 08/10/2026).

Les listes de runs (`usage.list_runs` : `/api/admin/usage/runs`,
`/api/orgs/{id}/monitoring/runs`, `op=runs` des deux consoles ; `usage.recent_runs` : le
bloc C injecté à chaque session MCP) prennent leurs N dernières ouvertures `run_start`
AVANT toute reconstruction (`usage._derniers_runs`). Sans index dédié, PostgreSQL
parcourait `idx_tool_calls_created_at` ou `idx_tool_calls_org` à rebours en écartant
tout ce qui n'est pas une ouverture. Mesuré en production (lecture seule, 09/10/2026) :
5,1 s, 301 819 buffers et 319 906 lignes écartées pour la page plateforme (LIMIT 100) ;
coupée à 15 s pour la plus grosse org, et pour l'agent hébergé le plus actif (LIMIT 5).

Les ouvertures ne sont jamais archivées (~98 000 lignes sur ~12,2 M, 0,8 %) : chaque
index est petit (quelques Mo) et rangé dans l'ordre EXACT de la page
(`created_at DESC, id DESC` derrière l'égalité de portée) — un parcours d'index seul qui
s'arrête au `LIMIT`. Leur forme et leur prédicat vivent dans
`oto_mcp/db/index_releve.py` (`OUVERTURES`, `PREDICAT_OUVERTURE`), partagés avec la
requête et avec le démarrage d'une base neuve.

**Posés CONCURRENTLY**, sous le verdict commun (`db/index_concurrent.py`) : la révision ne
construit que sur une `tool_calls` PETITE (base neuve, de test) ; sur la base servie
(~12 M lignes), elle lève `ConstructionManuelleRequise` — chaque construction lit deux
fois toute la table, hors de la fenêtre d'une migration —, et le geste est manuel
(docs/migrations-versionnees.md §5.1). Rejouée après le geste, elle constate les trois
index valides et ne fait rien. Un index INVALIDE (construction interrompue) lève
`IndexInvalide`, jamais pris pour fait.

**Verrous.** `ShareUpdateExclusiveLock` sur `tool_calls` seulement : ni lectures ni
écritures bloquées, seulement un autre DDL ou un VACUUM sur la table. La phase
concurrente attend la fin des transactions plus anciennes qu'elle (`virtualxid`),
`lock_timeout` 5 min.

**Ordre avec le code : indifférent.** Le code servi depuis f550f246 lit les ouvertures
avec ces prédicats ; sans les index il répond juste, plus lentement. Prod et préprod
partagent la base : posés une fois, ils servent les deux.

Retour arrière : `DROP INDEX CONCURRENTLY IF EXISTS` des trois.

Révision : 0049_tool_calls_ouvertures_runs
Précédente : 0048_origine_ecritures_retiree
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db import index_concurrent, index_releve

revision = "0049_tool_calls_ouvertures_runs"
down_revision = "0048_origine_ecritures_retiree"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for index in index_releve.OUVERTURES:
        index_concurrent.poser_par_revision(op, index)


def downgrade() -> None:
    for index in reversed(index_releve.OUVERTURES):
        index_concurrent.retirer_par_revision(op, index)
