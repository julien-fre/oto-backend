"""Les favoris partagés d'une org (`org_favorites`) — le stockage seul, sans aucun droit.

Une ligne = une page (`doc`) ou un tableau (`table`) épinglé pour l'org, avec qui l'a
épinglé et quand. Qui peut LIRE quoi se juge dans la capacité
(`capabilities/orgs/favoris.py`) : ce module ne filtre rien.
"""
from __future__ import annotations

from typing import Any, Optional

from ._conn import _connect

KINDS = ("doc", "table")

_COLS = "org_id, kind, ref_id, added_by, added_at"


def lister(org_id: int) -> list[dict[str, Any]]:
    """Les favoris de l'org, dans l'ordre où ils ont été posés (le plus ancien d'abord)."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM org_favorites WHERE org_id = %s "
            "ORDER BY added_at, kind, ref_id", (int(org_id),)).fetchall()
    return [dict(r) for r in rows]


def poser(org_id: int, kind: str, ref_id: int, added_by: Optional[str]) -> None:
    """Épingle — idempotent : un favori déjà posé garde son auteur et sa date."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO org_favorites (org_id, kind, ref_id, added_by) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (org_id, kind, ref_id) DO NOTHING",
            (int(org_id), kind, int(ref_id), added_by))


def retirer(org_id: int, kind: str, ref_id: int) -> None:
    """Désépingle — idempotent."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM org_favorites WHERE org_id = %s AND kind = %s AND ref_id = %s",
            (int(org_id), kind, int(ref_id)))


def retirer_morts(kind: str, ref_ids: list[int]) -> None:
    """Retire, dans TOUTES les orgs, les favoris d'objets qui n'existent plus."""
    if not ref_ids:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM org_favorites WHERE kind = %s AND ref_id = ANY(%s)",
                     (kind, [int(r) for r in ref_ids]))
