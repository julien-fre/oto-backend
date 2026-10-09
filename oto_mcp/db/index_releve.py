"""Les index partiels de `tool_calls` qui rendent des lectures du journal légères.

**`idx_tool_calls_org_tool_ok`** — les relevés d'org par outil (oto-backend#1145).
`tool_calls (org_id, tool, created_at DESC) WHERE ok`. Toute base vivante le porte (posé
avant la référence du registre, docs/migrations-versionnees.md §5.4) ; une base neuve le
reçoit du démarrage (`_init.py`, non concurrent), sous le verdict commun des index de ce
régime (`db/index_concurrent.py`) : mesuré en
production le 04/10/2026, 172 s de construction pour environ 12 M lignes — au-delà des
120 s de la fenêtre de démarrage, d'où le seuil de taille et le geste manuel (§5.1).

**`OUVERTURES`** — le choix de page des listes de runs (infra#9, révision 0049).
`usage._derniers_runs` prend les N dernières ouvertures `run_start` d'une portée
(plateforme, org, compte × org) avant toute reconstruction. Sans index dédié, PostgreSQL
parcourait `idx_tool_calls_created_at` ou `idx_tool_calls_org` à rebours en écartant
tout ce qui n'est pas une ouverture : mesuré en production le 09/10/2026, 5,1 s et
300 000 lignes écartées pour la page plateforme, plus de 15 s pour la plus grosse org et
pour l'agent hébergé le plus actif. Les ouvertures ne sont jamais archivées (~100 000
lignes, 0,8 % du journal) : trois index partiels de quelques Mo, chacun dans l'ORDRE
EXACT du `ORDER BY created_at DESC, id DESC` de la page — un parcours d'index seul qui
s'arrête au `LIMIT`. Leur prédicat est celui de `_derniers_runs`, mot pour mot : un
prédicat d'index que la requête n'implique pas ne sert aucune lecture.
"""
from __future__ import annotations

from .index_concurrent import IndexConcurrent

RELEVE = IndexConcurrent(
    nom="idx_tool_calls_org_tool_ok",
    table="tool_calls",
    forme="(org_id, tool, created_at DESC) WHERE ok",
)

#: Le prédicat d'une OUVERTURE de run, partagé par les index et par la requête
#: (`usage._derniers_runs`, sur l'alias `d`).
PREDICAT_OUVERTURE = "tool = 'run_start' AND run_id IS NOT NULL"

#: La révision qui pose les index d'ouvertures.
REVISION_OUVERTURES = "0049_tool_calls_ouvertures_runs"

OUVERTURES: tuple[IndexConcurrent, ...] = (
    # La page plateforme (`list_runs` sans org).
    IndexConcurrent(nom="idx_tool_calls_run_start", table="tool_calls",
                    forme=f"(created_at DESC, id DESC) WHERE {PREDICAT_OUVERTURE}",
                    revision=REVISION_OUVERTURES),
    # La page d'une org (`list_runs(org_id=…)`).
    IndexConcurrent(nom="idx_tool_calls_run_start_org", table="tool_calls",
                    forme=f"(org_id, created_at DESC, id DESC) WHERE {PREDICAT_OUVERTURE}",
                    revision=REVISION_OUVERTURES),
    # La page d'un compte dans une org, ou hors org (`recent_runs`, bloc C du handshake).
    IndexConcurrent(nom="idx_tool_calls_run_start_sub", table="tool_calls",
                    forme=f"(sub, org_id, created_at DESC, id DESC) WHERE {PREDICAT_OUVERTURE}",
                    revision=REVISION_OUVERTURES),
)
