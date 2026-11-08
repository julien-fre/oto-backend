"""Les CORPS des versions passées d'une procédure — pour juger son dessin.

Couture ouverte le 08/11/2026 : `list_instruction_versions` ne rend que les
métadonnées (le panneau d'historique n'a pas besoin des corps), et la page du process
doit savoir si le dessin date d'avant les étapes qu'il montre
(`procedure_diagram.etat_du_dessin`). Lecture seule, bornée : on ne remonte jamais
au-delà de `limite` versions, une procédure en a rarement plus de quelques dizaines.

⚠️ **Les références au frère passent par `instructions.<nom>`** (cf.
`instructions_cycle.py`) — gardé par `tests/test_org_store_surface_frozen.py`.
"""
from __future__ import annotations

from . import instructions
from ..db import _connect


def instruction_revision_bodies(owner_type: str, owner_id: int | str, slug: str,
                                limite: int = 50) -> list[dict]:
    """`[{version, body_md}]`, la plus récente d'abord, `limite` au plus. [] pour le
    readme (pas d'historique, ADR 0042) ou un slug sans version."""
    otype, oid = instructions._owner(owner_type, owner_id)
    slug = instructions.normalize_slug(slug)
    if slug == instructions.BASE_SLUG:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT version, body_md FROM org_instruction_revisions "
            f"WHERE {instructions._OWNER_WHERE} AND slug = %s "
            "ORDER BY version DESC LIMIT %s",
            (otype, oid, slug, max(1, int(limite))),
        ).fetchall()
        return [dict(r) for r in rows]
