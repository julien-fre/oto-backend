"""« Dernières modifications » — pages et procédures, fusionnées par `updated_at`.

Une lecture DÉRIVÉE (oto#191) : aucune table, aucun journal — les colonnes
`updated_at` que les deux stores tiennent déjà, lues en UNE requête (`UNION ALL`
+ `ORDER BY … LIMIT`), pour que le tri et la coupe se fassent en base et non
après avoir rapatrié deux listes entières.

Le PÉRIMÈTRE n'est pas calculé ici : l'appelant passe les projets lisibles
(`ownership.accessible_project_ids`) et les propriétaires de procédures à portée
(`ownership.project_scope_owners` + la personne). Même règle que la recherche
(`db/search.py`) — « cherchable ⇔ lisible » devient « listé ⇔ lisible ».

⚠️ **L'auteur n'est jamais déduit.** Une PAGE porte le sien depuis oto#274 :
`docs.updated_by`, posé dans la même instruction que `updated_at` (création =
créateur, écriture = son compte, déplacement = NULL). L'appariement aux révisions
qu'on faisait avant ne tient plus : une rafale d'enregistrements du dashboard ne
crée plus d'instantané à chaque écriture, et la date de la page n'aurait plus eu
d'auteur. Une PROCÉDURE, elle, n'a que `org_instructions.set_by`, qui survit à un
déplacement (`instruction_ownership.move`) touchant `updated_at` sans le réécrire :
son auteur s'apparie EXACTEMENT à la révision insérée dans la même transaction
(`NOW()` y vaut la même valeur, à la microseconde) — une égalité de timestamps, pas
une fenêtre. Quand rien ne s'apparie (procédure transférée, page déplacée, écrivain
inconnu), l'auteur est `NULL` — l'écran affiche une date sans nom, jamais un nom faux.

L'auteur se cherche APRÈS la coupe : la sous-requête `m` trie et coupe sur les seules
colonnes de tri, et l'appariement aux révisions ne tourne que sur les `limit` lignes
gardées — pas sur chaque page des projets lisibles, pour un îlot chargé à chaque accueil.
"""
from __future__ import annotations

from ._conn import _connect

# Les deux branches rendent les MÊMES colonnes, dans le même ordre : c'est la
# condition du `UNION ALL`, et ce que l'appariement de l'auteur lit ensuite.
_DOCS = """
SELECT 'doc' AS type, d.id AS id, d.title AS title,
       d.project_id AS project_id, p.name AS project_name,
       NULL::text AS slug, NULL::text AS owner_type, NULL::text AS owner_id,
       NULL::integer AS version, d.updated_by AS updated_by,
       d.created_at AS created_at, d.updated_at AS updated_at
FROM docs d
JOIN projects p ON p.id = d.project_id
WHERE d.project_id = ANY(%s)
"""

# `slug <> 'claude_md'` et `archived_at IS NULL` : les deux prédicats des lectures du
# store (`list_instructions`, `list_instructions_for_owners`) — une archivée « cesse
# d'être proposée », et le readme pré-0042 n'est pas une procédure.
_PROCEDURES = """
SELECT 'procedure' AS type, oi.id AS id, oi.title AS title,
       NULL::bigint AS project_id, NULL::text AS project_name,
       oi.slug AS slug, oi.owner_type AS owner_type, oi.owner_id AS owner_id,
       oi.version AS version, NULL::text AS updated_by,
       oi.created_at AS created_at, oi.updated_at AS updated_at
FROM org_instructions oi
JOIN unnest(%s::text[], %s::text[]) AS o(t, i)
  ON oi.owner_type = o.t AND oi.owner_id = o.i
WHERE oi.slug <> %s AND oi.archived_at IS NULL
"""

_AUTEUR = """
CASE WHEN m.type = 'doc' THEN m.updated_by
     ELSE (SELECT r.set_by FROM org_instruction_revisions r
           WHERE r.owner_type = m.owner_type AND r.owner_id = m.owner_id
             AND r.slug = m.slug AND r.version = m.version
             AND r.created_at = m.updated_at
           LIMIT 1)
END
"""

_ORDRE = "m.updated_at DESC, m.type, m.id DESC"


def recent_changes(project_ids: list[int], procedure_owners: list[tuple[str, str]],
                   *, limit: int, base_slug: str) -> list[dict]:
    """Les `limit` dernières modifications parmi les pages de `project_ids` et les
    procédures possédées par `procedure_owners`, les plus récentes d'abord.

    Deux périmètres vides = aucune requête : la liste vide est une réponse, pas un
    parcours. Un seul des deux vide = sa branche ne rend rien (`ANY('{}')`,
    `unnest` de tableaux vides), sans cas particulier."""
    if not project_ids and not procedure_owners:
        return []
    sql = (
        "SELECT m.type, m.id, m.title, m.project_id, m.project_name, m.slug, "
        f"m.owner_type, m.updated_at, {_AUTEUR} AS author_sub "
        f"FROM (SELECT * FROM (({_DOCS}) UNION ALL ({_PROCEDURES})) AS m "
        f"ORDER BY {_ORDRE} LIMIT %s) AS m "
        f"ORDER BY {_ORDRE}"
    )
    params = (
        [int(p) for p in project_ids],
        [t for t, _ in procedure_owners], [i for _, i in procedure_owners],
        base_slug, int(limit),
    )
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
