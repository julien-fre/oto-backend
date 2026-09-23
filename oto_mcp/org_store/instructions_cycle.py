"""Le CYCLE DE VIE d'une procédure : la retirer du service, l'y remettre, la
détruire.

Couture extraite d'`instructions.py` le 10/09/2026, quand l'ajout du désarchivage
(#857) a fait passer ce module au-dessus du plafond de 500 lignes. Les trois verbes
qui forment ce cycle sont **les seuls** à décider si une procédure est proposée ou
non : les regrouper met côte à côte l'archivage et son inverse, dont l'asymétrie est
précisément ce qui a fabriqué le cas mesuré.

⚠️ **Les références au frère passent par `instructions.<nom>`, jamais par un import
à plat.** La façade du package reporte ses écritures de test sur
`instructions.<nom>` : un module qui aurait copié la fonction dans son propre
espace de noms ne verrait jamais le remplacement. La règle est gardée par
`tests/test_org_store_surface_frozen.py`.
"""
from __future__ import annotations

from typing import Optional

from . import instructions
from ..db import _connect


def archive_instruction(owner_type: str, owner_id: int | str, slug: str) -> bool:
    """Archive une procédure (soft-delete) : elle sort de tous les listings, la
    ligne et ses révisions restent. False si elle n'existait pas.

    Idempotent en pratique — ré-archiver rafraîchit l'horodatage plutôt que
    d'échouer, le résultat visé (« elle n'est plus en service ») étant déjà
    atteint. L'inverse est `unarchive_instruction`, juste en dessous. Ce qu'archiver
    garantit ici, c'est que RIEN n'est détruit — contrairement à
    `delete_instruction`, qui emporte l'historique."""
    otype, oid = instructions._owner(owner_type, owner_id)
    slug = instructions.normalize_slug(slug)
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE org_instructions SET archived_at = NOW(), updated_at = NOW() "
            f"WHERE {instructions._OWNER_WHERE} AND slug = %s", (otype, oid, slug)
        )
        return (cur.rowcount or 0) > 0


def unarchive_instruction(owner_type: str, owner_id: int | str,
                          slug: str) -> Optional[str]:
    """Remet une instruction EN SERVICE. Rend la date d'archivage qu'elle portait,
    ou `None` si elle n'était pas archivée (ou n'existe pas).

    ⚠️ **L'inverse de `archive_instruction` n'existait pas, et c'était un choix
    ASSUMÉ** — parité avec les projets, dont l'archivage n'avait pas d'inverse non
    plus. Rompu pour les procédures le 10/09/2026, puis pour les projets le 23/09
    (`db/projects.unarchive_project`, oto#38 — clôt oto-backend#929) : les deux
    archivages ont de nouveau la même forme, et leur inverse.

    ⚠️ **Rend la date d'AVANT, pas un booléen** : ressusciter une ligne que
    quelqu'un a retirée exprès doit laisser savoir QUAND elle l'avait été, sinon le
    journal dit qu'on a agi sans dire ce qu'on a annulé. `None` distingue « rien à
    défaire » de « défait » sans lever — remettre en service ce qui l'est déjà est
    un non-geste.
    """
    otype, oid = instructions._owner(owner_type, owner_id)
    slug = instructions.normalize_slug(slug)
    with _connect() as conn:
        # ⚠️ `RETURNING archived_at` rendrait la valeur NEUVE, donc NULL : on
        # annoncerait « rien à défaire » juste après avoir défait quelque chose. La
        # date d'avant est donc lue par une CTE, dans la MÊME instruction — et la
        # jointure passe par la clé NATURELLE, la seule qui désigne une instruction
        # partout ailleurs ici.
        row = conn.execute(
            "WITH avant AS ("
            "  SELECT owner_type, owner_id, slug, archived_at FROM org_instructions "
            f"  WHERE {instructions._OWNER_WHERE} AND slug = %s AND archived_at IS NOT NULL"
            ") "
            "UPDATE org_instructions o SET archived_at = NULL, updated_at = NOW() "
            "FROM avant a WHERE o.owner_type = a.owner_type "
            "  AND o.owner_id = a.owner_id AND o.slug = a.slug "
            "RETURNING a.archived_at AS avant",
            (otype, oid, slug),
        ).fetchone()
        return None if row is None else row["avant"]


def delete_instruction(owner_type: str, owner_id: int | str, slug: str) -> bool:
    """Supprime une instruction ET son historique. False si elle n'existait pas."""
    otype, oid = instructions._owner(owner_type, owner_id)
    slug = instructions.normalize_slug(slug)
    with _connect() as conn:
        with conn.transaction():
            cur = conn.execute(
                f"DELETE FROM org_instructions WHERE {instructions._OWNER_WHERE} AND slug = %s",
                (otype, oid, slug),
            )
            removed = (cur.rowcount or 0) > 0
            conn.execute(
                f"DELETE FROM org_instruction_revisions WHERE {instructions._OWNER_WHERE} AND slug = %s",
                (otype, oid, slug),
            )
    return removed
