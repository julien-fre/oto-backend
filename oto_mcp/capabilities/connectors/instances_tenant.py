"""La ligne TENANT de `oto_instance op=list` (L-clés PR 2).

Un compte d'un tenant tiers voit la clé de son tenant comme une instance de niveau
`tenant` — entre l'org et la plateforme, comme dans le walker. Un compte nu ne la
voit pas : il ne pourrait pas la résoudre (`tenant_vault.rung_tenant` rend None), et
« qui peut la résoudre la voit » (R9). Module à part pour ne pas alourdir la
projection principale (605 lignes) : il rend les LIGNES, la projection reste là-bas.
"""
from __future__ import annotations

from typing import Optional

from ... import credentials_store, instance_refs, tenant_vault


def tenant_rows(sub: Optional[str]) -> list[tuple[str, str, dict]]:
    """`(slug, ref, ligne de coffre)` pour chaque clé du tenant de `sub` — vide pour
    un compte nu ou anonyme."""
    slug = tenant_vault.rung_tenant(sub)
    if slug is None:
        return []
    out = []
    for row in credentials_store.list_credentials(credentials_store.TENANT, slug):
        ref = instance_refs.make_tenant_ref(slug, row["connector"], row.get("account") or "")
        out.append((slug, ref, row))
    return out
