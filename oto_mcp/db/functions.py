"""Le stockage des fonctions (ADR 0073) : identité, versions, décision.

Ici le SQL et rien d'autre : qui a le droit de lire, proposer ou publier se juge dans
`capabilities/functions.py`, et ce qu'une version contient (noms de fichiers, point
d'entrée, dépendances) s'y valide aussi.

⚠️ **Une version est IMMUABLE.** Ce module n'a aucune fonction qui réécrit les sources
d'une version : corriger, c'est en proposer une nouvelle. Seuls changent son statut et,
côté fonction, le pointeur vers la version publiée.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

import psycopg

from ._conn import _connect

# Ce que la liste et l'historique rendent d'une version : tout SAUF le code, qui peut
# peser des centaines de kilo-octets et ne se lit qu'en demandant CETTE version.
_VERSION_COLS = ("v.version, v.status, v.entrypoint, v.requirements, v.note, "
                 "v.proposed_by, v.proposed_at, v.decided_by, v.decided_at, v.test_report")
_FUNCTION_COLS = ("f.id, f.owner_type, f.owner_id, f.slug, f.title, f.description, "
                  "f.published_version, f.created_by, f.created_at, f.updated_at")


class FunctionExists(Exception):
    """Ce propriétaire a déjà une fonction de ce slug."""


class VersionConflict(Exception):
    """La dernière version n'est pas celle que l'appelant a lue."""

    def __init__(self, attendue: int, courante: int):
        super().__init__(f"dernière version {courante}, l'appelant a lu la {attendue}")
        self.attendue, self.courante = attendue, courante


def create_function(*, owner_type: str, owner_id: str, slug: str, title: str,
                    description: str, created_by: str, sources: dict, entrypoint: str,
                    requirements: list[str], note: Optional[str]) -> dict:
    """Crée la fonction ET sa version 1, proposée, dans une seule transaction.

    Une fonction sans version n'aurait rien à montrer ni à juger : les deux naissent
    ensemble, ou rien.
    """
    with _connect() as conn:
        with conn.transaction():
            try:
                fn = conn.execute(
                    "INSERT INTO functions (owner_type, owner_id, slug, title, description, "
                    "created_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (owner_type, str(owner_id), slug, title, description or "", created_by),
                ).fetchone()
            except psycopg.errors.UniqueViolation as e:
                raise FunctionExists(slug) from e
            _insert_version(conn, fn["id"], 1, sources=sources, entrypoint=entrypoint,
                            requirements=requirements, note=note, proposed_by=created_by)
    return get_function(owner_type, owner_id, slug)


def _insert_version(conn, function_id: int, version: int, *, sources: dict,
                    entrypoint: str, requirements: list[str], note: Optional[str],
                    proposed_by: str) -> None:
    conn.execute(
        "INSERT INTO function_versions (function_id, version, sources, entrypoint, "
        "requirements, note, proposed_by) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)",
        (function_id, version, json.dumps(sources), entrypoint, list(requirements),
         note, proposed_by))
    conn.execute("UPDATE functions SET updated_at = NOW() WHERE id = %s", (function_id,))


def propose_version(function_id: int, *, expected_version: int, sources: dict,
                    entrypoint: str, requirements: list[str], note: Optional[str],
                    proposed_by: str) -> int:
    """Ajoute la version suivante, PROPOSÉE. Rend son numéro.

    `expected_version` est la dernière version que l'appelant a lue. Si une autre
    proposition est passée entre-temps, on refuse plutôt que d'empiler deux versions
    écrites chacune sans voir l'autre. La ligne de la fonction est verrouillée le
    temps du calcul du numéro : deux propositions simultanées ne prennent pas le même.
    """
    with _connect() as conn:
        with conn.transaction():
            conn.execute("SELECT id FROM functions WHERE id = %s FOR UPDATE", (function_id,))
            derniere = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM function_versions "
                "WHERE function_id = %s", (function_id,)).fetchone()["v"]
            if derniere != expected_version:
                raise VersionConflict(expected_version, derniere)
            _insert_version(conn, function_id, derniere + 1, sources=sources,
                            entrypoint=entrypoint, requirements=requirements, note=note,
                            proposed_by=proposed_by)
    return derniere + 1


def get_function(owner_type: str, owner_id: str, slug: str) -> Optional[dict]:
    """La fiche d'une fonction et le numéro de sa dernière version, ou `None`."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_FUNCTION_COLS}, "
            "  (SELECT MAX(version) FROM function_versions v WHERE v.function_id = f.id) "
            "  AS latest_version "
            "FROM functions f WHERE f.owner_type = %s AND f.owner_id = %s AND f.slug = %s",
            (owner_type, str(owner_id), slug)).fetchone()
    return dict(row) if row else None


def list_functions(owners: Iterable[tuple[str, str]]) -> list[dict]:
    """Les fonctions de ces propriétaires, par slug. Une requête, sans le code."""
    owners = list(owners)
    if not owners:
        return []
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_FUNCTION_COLS}, "
            "  (SELECT MAX(version) FROM function_versions v WHERE v.function_id = f.id) "
            "  AS latest_version "
            "FROM functions f WHERE (f.owner_type, f.owner_id) IN "
            f"({','.join(['(%s, %s)'] * len(owners))}) ORDER BY f.slug",
            [v for pair in owners for v in pair]).fetchall()
    return [dict(r) for r in rows]


def get_version(function_id: int, version: int) -> Optional[dict]:
    """UNE version, code compris, ou `None`."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_VERSION_COLS}, v.sources FROM function_versions v "
            "WHERE v.function_id = %s AND v.version = %s", (function_id, version)).fetchone()
    return dict(row) if row else None


def list_versions(function_id: int) -> list[dict]:
    """L'historique, de la plus récente à la plus ancienne, sans le code."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_VERSION_COLS} FROM function_versions v WHERE v.function_id = %s "
            "ORDER BY v.version DESC", (function_id,)).fetchall()
    return [dict(r) for r in rows]


def decide_version(function_id: int, version: int, *, status: str, decided_by: str,
                   test_report: Optional[dict]) -> None:
    """Publie ou refuse UNE version, dans une transaction.

    `publiee` pointe la fonction sur cette version : c'est aussi le RETOUR ARRIÈRE —
    republier une version antérieure la remet en service, sans rien réécrire.
    `refusee` ne touche pas au pointeur : la version en service reste en service.
    Le motif d'un refus voyage dans `test_report` : la version elle-même ne change pas.
    """
    if status not in ("publiee", "refusee"):
        raise ValueError(f"statut de décision inconnu : {status}")
    with _connect() as conn:
        with conn.transaction():
            conn.execute(
                "UPDATE function_versions SET status = %s, decided_by = %s, "
                "decided_at = NOW(), test_report = %s::jsonb "
                "WHERE function_id = %s AND version = %s",
                (status, decided_by, json.dumps(test_report) if test_report else None,
                 function_id, version))
            if status == "publiee":
                conn.execute("UPDATE functions SET published_version = %s, "
                             "updated_at = NOW() WHERE id = %s", (version, function_id))
