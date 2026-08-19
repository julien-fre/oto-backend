"""Lire UN nœud : sa fiche, son corps en blocs, son fil, le schéma de ses colonnes.

Deuxième surface de lecture du modèle de nœuds, après le rail (`db/shell.py`), et
même discipline : lecture SEULE, et `kind <> 'ligne'` dans chaque requête.

⚠️ **Le prédicat de genre est ici pour la MÊME double raison qu'au rail** — le modèle
(0054-D4 : une ligne de tableau n'est pas un objet qu'on ouvre, elle est le CONTENU de
son tableau) et l'index partiel `idx_nodes_owner_scoped`, qu'une lecture sans genre ne
peut pas utiliser. Le détail est écrit une fois, dans `db/shell.py` : le relire là.

⚠️ **On ne lit JAMAIS les lignes d'un tableau ici.** Ouvrir un tableau rend son SCHÉMA
de colonnes, pas ses 43 584 lignes : les lignes ont leur propre surface, paginée par
curseur (lot ⑤). Un « ouvrir » qui ramène tout le contenu n'est pas une lecture de
fiche, c'est un export déguisé.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect

_HORS_LIGNES = "n.kind <> 'ligne'"

_FICHE = ("n.id, n.public_id, n.parent_id, n.kind, n.owner_type, n.owner_id, "
          "n.position, n.props, n.created_at, n.updated_at")


def node_by_public_id(public_id: str) -> Optional[dict]:
    """La fiche d'un nœud, ou `None`. Rien d'autre — l'accès se juge au-dessus."""
    if not public_id:
        return None
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_FICHE} FROM nodes n "
            f"WHERE {_HORS_LIGNES} AND n.public_id = %s", (public_id,)).fetchone()
    return dict(row) if row else None


def blocks_of(node_id: int) -> list[dict]:
    """Le corps, DANS L'ORDRE — l'unique question qu'on pose à `blocks`.

    `public_id` est un TIRAGE conservé à la re-projection (#362) : il survit à une
    insertion et à un déplacement, donc il est ancrable. C'est ce qui autorise le
    front à y accrocher un commentaire ou une trace d'exécution.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT public_id, position, type, props FROM blocks "
            "WHERE node_id = %s ORDER BY position", (node_id,)).fetchall()
    return [dict(r) for r in rows]


def ancestors_of(node_id: int, *, max_depth: int = 12) -> list[dict]:
    """La chaîne des parents, de la RACINE jusqu'au nœud inclus.

    Récursive en SQL et BORNÉE : `nodes.parent_id` n'a pas de clé étrangère (arbitrage
    M-e ouvert), donc rien en base n'empêche un cycle. Une remontée non bornée
    tournerait jusqu'au timeout — la borne est là pour que la panne soit un fil tronqué,
    pas un serveur qui pend.
    """
    with _connect() as conn:
        rows = conn.execute(
            "WITH RECURSIVE chaine AS ("
            "  SELECT n.id, n.public_id, n.parent_id, n.kind, n.props, 0 AS niveau"
            f"   FROM nodes n WHERE n.id = %s AND {_HORS_LIGNES}"
            "  UNION ALL"
            "  SELECT p.id, p.public_id, p.parent_id, p.kind, p.props, c.niveau + 1"
            "    FROM nodes p JOIN chaine c ON p.id = c.parent_id"
            "   WHERE p.kind <> 'ligne' AND c.niveau < %s"
            ") SELECT * FROM chaine ORDER BY niveau DESC", (node_id, max_depth)).fetchall()
    return [dict(r) for r in rows]


def siblings_of(parent_ids: list[Optional[int]], *, owner: tuple[str, str],
                cap: int = 50) -> dict:
    """`{parent_id: [frères]}` pour les maillons du fil — en UNE requête.

    Le contrat du front veut la fratrie de CHAQUE maillon (« la réponse voyage avec la
    question, l'ouverture d'un popover ne demande rien »). Une requête par niveau
    ferait N+1 sur un chemin ouvert à chaque navigation ; un seul `IN` les rend toutes.

    ⚠️ `owner` borne la fratrie au propriétaire du nœud : sans lui, deux organisations
    dont les arbres ont des racines `parent_id IS NULL` se verraient l'une l'autre dans
    le popover. C'est le genre de fuite qu'un `ORDER BY position` ne montre jamais.
    """
    reels = [p for p in dict.fromkeys(parent_ids) if p is not None]
    racines = any(p is None for p in parent_ids)
    if not reels and not racines:
        return {}
    clauses, params = [], []
    if reels:
        clauses.append("n.parent_id = ANY(%s)")
        params.append(reels)
    if racines:
        clauses.append("n.parent_id IS NULL")
    params += [owner[0], owner[1]]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT n.parent_id, n.public_id, n.kind, n.props->>'title' AS title "
            f"FROM nodes n WHERE {_HORS_LIGNES} AND ({' OR '.join(clauses)}) "
            "AND n.owner_type = %s AND n.owner_id = %s "
            "ORDER BY n.position NULLS LAST, n.props->>'title'", params).fetchall()
    out: dict = {}
    for r in rows:
        seau = out.setdefault(r["parent_id"], [])
        if len(seau) < cap:
            seau.append(dict(r))
    return out
