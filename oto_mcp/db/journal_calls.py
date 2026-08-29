"""Le journal des appels lu par ses filtres — et ce que le scope d'org laisse dehors (#630).

Vécu en production le 29/08/2026 : un `data_write` refusé à 21:11:23, présent dans le
déroulé de son run (`op=run`, 17 appels), absent de la vue filtrée `op=calls
org_id=226` interrogée trois fois avec des motifs que son texte contient. Rejoué contre
la base : avec `org_id=226` la vue rend 1 refus portant le nom du tableau ; sans, 40.
La condition qui l'élimine est le SCOPE de la vue — `tool_calls.org_id = 226` — parce
que l'appel a été RÉSOLU sous l'org maison de l'appelant (2), l'axe `_org` étant absent
(#631). La vue était exacte dans son périmètre ; le lecteur ne savait pas ce que ce
périmètre laissait dehors, et un « zéro » lu là était muet.

Ce module rend le plancher comparable : `count_calls_of_org_runs_elsewhere` compte les
appels des RUNS de l'org (`runs.org_id`, gelé à `run_start`) stampés sous une AUTRE org,
sous LES MÊMES filtres que la page. Pour que ce soit vrai par construction, la
construction des filtres de la page (`usage.list_tool_calls`) vit ici,
`call_filter_clauses`, et les deux l'appellent : deux constructions divergent en silence.

Mesuré sur la base de prod (29/08, 1,17 M de lignes, org de 10 057 runs) : 28 ms sur une
fenêtre d'un jour, merge join `runs` (seq scan, 10 k lignes) ⨝ `idx_tool_calls_run`.
Le coût est linéaire en fenêtre : d'où `since` OBLIGATOIRE — sans borne, le compte
parcourrait tout le journal à chaque rafraîchissement du tableau de bord (une requête
par minute, vue dans le journal REST).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ._conn import _connect


def call_filter_clauses(
    *, sub: Optional[str] = None, tool_name: Optional[str] = None,
    errors_only: bool = False, since_days: Optional[int] = None,
    run_id: Optional[str] = None, session_id: Optional[str] = None,
    min_duration_ms: Optional[int] = None, error_contains: Optional[str] = None,
) -> tuple[list[str], list[Any]]:
    """Les clauses WHERE d'une lecture filtrée du journal, alias `l` — SANS le scope
    d'org ni le `kind`, que chaque lecteur pose lui-même (c'est précisément ce qui
    distingue la page de son plancher)."""
    clauses: list[str] = []
    params: list[Any] = []
    if sub:
        clauses.append("l.sub = %s")
        params.append(sub)
    if tool_name:
        clauses.append("l.tool = %s")
        params.append(tool_name)
    if errors_only:
        clauses.append("l.ok = FALSE")
    if since_days is not None:
        clauses.append("l.created_at >= NOW() - make_interval(days => %s)")
        params.append(int(since_days))
    if run_id:
        clauses.append("l.run_id = %s")
        params.append(run_id)
    if session_id:
        clauses.append("l.session_id = %s")
        params.append(session_id)
    if min_duration_ms is not None:
        clauses.append("l.duration_ms >= %s")
        params.append(int(min_duration_ms))
    if error_contains:
        clauses.append("l.error ILIKE %s")
        params.append(f"%{error_contains}%")
    return clauses, params


def count_calls_of_org_runs_elsewhere(org_id: int, *, since: datetime,
                                      **filters: Any) -> int:
    """Combien d'appels des runs de `org_id` ont été résolus sous une autre org depuis
    `since`, sous les mêmes filtres que la page (`call_filter_clauses`).

    « Autre org » inclut l'appel sans org (`org_id NULL`, `IS DISTINCT FROM`). Un run
    d'une autre org ne compte jamais, même si l'appelant est membre ici : le scope de la
    vue d'org, c'est ce qui a été émis SOUS l'org — le plancher garde la même règle, il
    la complète par la seule chose que l'org possède aussi : ses runs."""
    clauses, params = call_filter_clauses(**filters)
    clauses = ["l.kind = 'mcp'", "r.org_id = %s", "l.org_id IS DISTINCT FROM %s",
               "l.created_at >= %s", *clauses]
    params = [int(org_id), int(org_id), since, *params]
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT count(*) AS n
            FROM tool_calls l JOIN runs r ON r.run_id = l.run_id
            WHERE {" AND ".join(clauses)}
            """,
            tuple(params),
        ).fetchone()
    return int(row["n"]) if row else 0
