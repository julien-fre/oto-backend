"""Les partages d'UNE page (`resource_grants.resource_type = 'doc'`, signal #1084).

Lus depuis les deux bouts : ce qu'une personne REÇOIT (`list_docs_granted_to`, sa liste
« partagées avec moi »), et ce qu'un propriétaire a partagé page par page
(`list_shared_docs`, la liste de gouvernance d'`oto_resource`). Un partage échu
(otomata-tech/oto#39) ne compte dans aucune des deux : la page n'est plus partagée. Écrire et révoquer ne
passent pas ici : ce sont les grants génériques (`grant_resource`,
`revoke_resource_grant`), comme pour les trois autres familles.

⚠️ **Jamais le corps.** Ces lignes nomment une page (id, titre, chapô, date) ; le contenu
se lit par `oto_doc op=get`, derrière sa garde. Une liste qui porterait `body_md`
contournerait la seule porte qui décide qui lit quoi.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect
from ._partage_vivant import PARTAGE_VIVANT_G

# = `capabilities/docs/common.DOC_RTYPE`, recopié : la couche `db` ne remonte jamais
# chercher une constante dans les capacités (ADR 0004, sens unique).
_RTYPE_PAGE = "doc"


def list_docs_granted_to(principals: list[tuple[str, str]]) -> list[dict]:
    """Les pages partagées à l'un de ces principals — UNE ligne par page, même si deux
    principals de l'appelant la reçoivent (son org et lui) : le grant le plus ancien la
    représente. Pages de projets archivés exclues, comme pour les projets reçus
    (`list_projects_granted_to`). Plus récentes d'abord."""
    if not principals:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (d.id) d.id, d.project_id, d.title, d.description, "
            "       d.updated_at, g.role, g.principal_type, g.granted_by, g.granted_at "
            "FROM resource_grants g "
            "JOIN docs d ON d.id::text = g.resource_id "
            "JOIN projects p ON p.id = d.project_id "
            "JOIN unnest(%s::text[], %s::text[]) AS pr(t, i) "
            "  ON g.principal_type = pr.t AND g.principal_id = pr.i "
            f"WHERE g.resource_type = %s AND p.archived_at IS NULL AND {PARTAGE_VIVANT_G} "
            "ORDER BY d.id, g.granted_at",
            ([p[0] for p in principals], [p[1] for p in principals], _RTYPE_PAGE),
        ).fetchall()
    return sorted((dict(r) for r in rows), key=lambda r: r["updated_at"], reverse=True)


def list_shared_docs(owners: Optional[list[tuple[str, str]]]) -> list[dict]:
    """Les pages qui portent AU MOINS UN partage, dans les projets de ces propriétaires
    (`None` = tous : la vue plateforme). Une page sans partage n'y est pas : la liste
    répond à « qu'ai-je partagé page par page ? », pas à « quelles pages existent ? »."""
    if owners is not None and not owners:
        return []
    sql = ("SELECT d.id, d.project_id, d.title, d.updated_at, p.owner_type, p.owner_id "
           "FROM docs d JOIN projects p ON p.id = d.project_id ")
    args: list = []
    if owners is not None:
        sql += ("JOIN unnest(%s::text[], %s::text[]) AS o(t, i) "
                "  ON p.owner_type = o.t AND p.owner_id = o.i ")
        args += [[o[0] for o in owners], [o[1] for o in owners]]
    sql += ("WHERE EXISTS (SELECT 1 FROM resource_grants g WHERE g.resource_type = %s "
            f"              AND g.resource_id = d.id::text AND {PARTAGE_VIVANT_G}) "
            "ORDER BY d.updated_at DESC")
    args.append(_RTYPE_PAGE)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
