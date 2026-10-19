"""runner_jobs : l'index partiel des travaux d'un DÉCLENCHEUR — le résumé de la page Agents.

Le résumé de la page Agents (`runner_jobs.resume_des_declencheurs` : cases par heure et
dernière panne de chaque agent) et le temps gagné (`outils_des_runs_conclus`) lisent les
travaux d'une org sur une fenêtre qui portent une clé `trigger_id`. Sans index dédié, la
seule voie était un parcours complet de la file. Mesuré en production (lecture seule,
18/10/2026) : 775 ms à froid, 42 ms à chaud, 86 616 lignes écartées, pour l'org la plus
chargée — dont tous les travaux viennent de flottes, et ne portent donc pas la clé.

`idx_runner_jobs_declencheurs (org_id, due_at)` ne garde que les travaux d'un
déclencheur, rangés par (org, échéance). Sa forme et son prédicat vivent dans
`oto_mcp/db/runner_jobs.py` (`INDEX_DECLENCHEURS`, `PREDICAT_DECLENCHEUR`), partagés avec
les trois requêtes et avec le démarrage d'une base neuve : le prédicat est celui des
requêtes, mot pour mot.

**Posé CONCURRENTLY**, sous le verdict commun (`db/index_concurrent.py`) : la révision
construit tant que `runner_jobs` reste sous le seuil de taille (~87 000 lignes le
18/10/2026, seuil 100 000) ; au-delà, elle lève `ConstructionManuelleRequise`, et le geste
est manuel (docs/migrations-versionnees.md §5.1) : `oto-mcp maintenance
index-concurrents 0067`, puis `oto-mcp migrer upgrade head`. Rejouée après le geste, elle
constate l'index valide et ne fait rien. Un index INVALIDE lève `IndexInvalide`, jamais
pris pour fait.

**Verrous.** `ShareUpdateExclusiveLock` sur `runner_jobs` seulement : ni lectures ni
écritures bloquées (la réservation des workers continue). `lock_timeout` 5 min.

**Ordre avec le code : indifférent.** Les requêtes répondent juste sans l'index, plus
lentement. Prod et préprod partagent la base : posé une fois, il sert les deux.

Retour arrière : `DROP INDEX CONCURRENTLY IF EXISTS idx_runner_jobs_declencheurs`.

Révision : 0067_runner_jobs_declencheurs
Précédente : 0066_orgs_time_saved_minutes
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db import index_concurrent, runner_jobs

revision = "0067_runner_jobs_declencheurs"
down_revision = "0066_orgs_time_saved_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    index_concurrent.poser_par_revision(op, runner_jobs.INDEX_DECLENCHEURS)


def downgrade() -> None:
    index_concurrent.retirer_par_revision(op, runner_jobs.INDEX_DECLENCHEURS)
