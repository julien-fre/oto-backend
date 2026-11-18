"""Les applications de Custom API d'une org (`api_apps`) — le stockage seul, sans droit.

Une ligne = une application : l'outil qu'une org a configuré une fois (son adresse, son
mode d'authentification, sa définition), servie à toutes les clés qui lui sont LIÉES,
quel que soit leur palier. Qui peut lire ou écrire se juge au-dessus
(`connectors/api_apps.py`, `capabilities/connectors/api_apps.py`) ; ce module ne filtre
rien et ne touche jamais au coffre, sauf pour LISTER, sans déchiffrer, les clés liées
(`cles_liees`).
"""
from __future__ import annotations

from typing import Any, Optional

from ._conn import _connect

CONNECTOR = "api"
#: La clé de `connector_credentials.meta` qui lie une clé à son application : son `id`.
META_KEY = "api_app"

_COLS = ("id, org_id, account, connection, definition, created_by, created_at, "
         "updated_by, updated_at, archived_at")


class NameTaken(ValueError):
    """Une application vivante porte déjà ce nom dans l'org."""


def get(app_id: int) -> Optional[dict[str, Any]]:
    """L'application, archivée comprise (une clé liée doit pouvoir nommer ce qui l'a
    retirée), ou None."""
    with _connect() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM api_apps WHERE id = %s",
                           (int(app_id),)).fetchone()
    return dict(row) if row else None


def vivante(org_id: int, account: str) -> Optional[dict[str, Any]]:
    """L'application VIVANTE de ce nom dans l'org, ou None."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_COLS} FROM api_apps WHERE org_id = %s AND account = %s "
            "AND archived_at IS NULL", (int(org_id), account)).fetchone()
    return dict(row) if row else None


def lister(org_id: int) -> list[dict[str, Any]]:
    """Les applications vivantes de l'org, par nom."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM api_apps WHERE org_id = %s AND archived_at IS NULL "
            "ORDER BY account", (int(org_id),)).fetchall()
    return [dict(r) for r in rows]


def creer(org_id: int, account: str, connection: dict, definition: dict,
          by: Optional[str]) -> dict[str, Any]:
    """Crée l'application ; `NameTaken` si une application vivante porte ce nom."""
    from psycopg import errors
    from psycopg.types.json import Jsonb
    try:
        with _connect() as conn:
            row = conn.execute(
                "INSERT INTO api_apps (org_id, account, connection, definition, "
                f"created_by, updated_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_COLS}",
                (int(org_id), account, Jsonb(connection), Jsonb(definition), by, by),
            ).fetchone()
    except errors.UniqueViolation:
        raise NameTaken(account)
    return dict(row)


def modifier(app_id: int, *, connection: Optional[dict] = None,
             definition: Optional[dict] = None, by: Optional[str]) -> Optional[dict]:
    """Change une application vivante : `connection` donnée la REMPLACE entière (elle
    arrive validée et complétée — un champ que le nouveau mode écarte doit partir),
    `definition` pose ses seules CLÉS (les autres restent). None si l'application
    n'existe pas ou est archivée."""
    from psycopg.types.json import Jsonb
    with _connect() as conn:
        row = conn.execute(
            "UPDATE api_apps SET connection = COALESCE(%s::jsonb, connection), "
            "definition = definition || %s::jsonb, updated_by = %s, updated_at = NOW() "
            f"WHERE id = %s AND archived_at IS NULL RETURNING {_COLS}",
            (Jsonb(connection) if connection is not None else None,
             Jsonb(definition or {}), by, int(app_id)),
        ).fetchone()
    return dict(row) if row else None


def archiver(app_id: int, by: Optional[str]) -> bool:
    """Retire l'application (archivée, jamais supprimée). True si elle était vivante."""
    with _connect() as conn:
        return conn.execute(
            "UPDATE api_apps SET archived_at = NOW(), updated_by = %s, updated_at = NOW() "
            "WHERE id = %s AND archived_at IS NULL", (by, int(app_id))).rowcount > 0


def cles_liees(app_id: int) -> list[dict[str, Any]]:
    """Les clés du coffre liées à cette application — propriétaire, compte, pose —,
    SANS déchiffrer. Une ligne par clé ; l'ordre : org, équipes, membres.
    ⚠️ Filtre sur `meta`, que rien n'indexe : un balayage des lignes `api` du coffre.
    Réservé aux gestes d'admin (la page d'une application, son retrait, un changement
    d'adresse) — jamais au chemin d'un appel ni d'une liste de membre (`liens_de`)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT entity_type, entity_id, account, set_at, set_by "
            "FROM connector_credentials WHERE connector = %s "
            "AND meta ->> %s = %s ORDER BY "
            "CASE entity_type WHEN 'org' THEN 0 WHEN 'group' THEN 1 ELSE 2 END, entity_id",
            (CONNECTOR, META_KEY, str(int(app_id)))).fetchall()
    return [dict(r) for r in rows]


def lien(entity_type: str, entity_id: str, account: str) -> Optional[int]:
    """L'`id` de l'application à laquelle cette clé du coffre est liée, ou None — lu
    dans `meta`, sans déchiffrer."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT meta ->> %s AS app FROM connector_credentials WHERE entity_type = %s "
            "AND entity_id = %s AND connector = %s AND account = %s",
            (META_KEY, entity_type, str(entity_id), CONNECTOR, account)).fetchone()
    try:
        return int(row["app"]) if row and row["app"] is not None else None
    except (TypeError, ValueError):
        return None


def liens_de(lignes: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Pour ces propriétaires du coffre (`(entity_type, entity_id)`), leurs clés `api` liées
    à une application : `{entity_type, entity_id, account, app_id}`. Une requête, par le
    préfixe de la clé primaire du coffre (`entity_type, entity_id, connector`), jamais par
    un balayage de `meta`."""
    if not lignes:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT entity_type, entity_id, account, (meta ->> %s)::bigint AS app_id "
            "FROM connector_credentials c "
            "JOIN unnest(%s::text[], %s::text[]) AS p(et, eid) "
            "  ON c.entity_type = p.et AND c.entity_id = p.eid "
            "WHERE c.connector = %s AND c.meta ? %s",
            (META_KEY, [t for t, _ in lignes], [str(i) for _, i in lignes], CONNECTOR,
             META_KEY)).fetchall()
    return [dict(r) for r in rows]
