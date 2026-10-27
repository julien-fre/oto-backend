"""Commentaires de ligne : lecture et écriture de `datastore_row_comments`.

NON aplati dans `db.*` : `lister`/`ajouter`/`retirer` sont trop communs pour la surface
plate. Les appelants écrivent `db.datastore_commentaires.<fn>`.

Un commentaire retiré garde sa ligne (`deleted_at`) et ne se sert plus :
aucune lecture d'ici ne le rend.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect

_COLONNES = "id, auteur_sub, body, created_at"


def lister(ns_id: int, row_id: str, *, avant_id: Optional[int] = None,
           limit: int = 50) -> list[dict]:
    """Les commentaires vivants d'une ligne, le plus récent d'abord (`id` décroissant),
    au plus `limit + 1` : celui de trop dit à l'appelant qu'il reste une page."""
    clauses = ["ns_id = %s", "row_id = %s", "deleted_at IS NULL"]
    params: list = [int(ns_id), str(row_id)]
    if avant_id is not None:
        clauses.append("id < %s")
        params.append(int(avant_id))
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {_COLONNES} FROM datastore_row_comments "
            f"WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT %s",
            (*params, int(limit) + 1)).fetchall()]


def ajouter(ns_id: int, row_id: str, *, auteur_sub: Optional[str], body: str) -> dict:
    with _connect() as conn:
        return dict(conn.execute(
            f"INSERT INTO datastore_row_comments (ns_id, row_id, auteur_sub, body) "
            f"VALUES (%s, %s, %s, %s) RETURNING {_COLONNES}",
            (int(ns_id), str(row_id), auteur_sub, body)).fetchone())


def lire(ns_id: int, row_id: str, comment_id: int) -> Optional[dict]:
    """Un commentaire VIVANT de cette ligne, ou `None`."""
    with _connect() as conn:
        r = conn.execute(
            f"SELECT {_COLONNES} FROM datastore_row_comments WHERE id = %s "
            f"AND ns_id = %s AND row_id = %s AND deleted_at IS NULL",
            (int(comment_id), int(ns_id), str(row_id))).fetchone()
    return dict(r) if r else None


def retirer(comment_id: int) -> bool:
    """Retire un commentaire vivant ; faux s'il l'était déjà (deux retraits croisés)."""
    with _connect() as conn:
        return conn.execute(
            "UPDATE datastore_row_comments SET deleted_at = now() "
            "WHERE id = %s AND deleted_at IS NULL", (int(comment_id),)).rowcount == 1
