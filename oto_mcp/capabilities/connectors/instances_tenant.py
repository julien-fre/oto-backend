"""The TENANT row of `oto_instance op=list` (L-keys PR 2).

An account of a third-party tenant sees its tenant's key as a `tenant`-level
instance — between the org and the platform, as in the walker. A bare account does not
see it: it could not resolve it (`tenant_vault.rung_tenant` returns None), and
"whoever can resolve it sees it" (R9). Separate module so as not to bloat the
main projection (605 lines): it returns the ROWS, the projection stays there.
"""
from __future__ import annotations

from typing import Optional

from ... import credentials_store, instance_refs, tenant_vault


def tenant_rows(sub: Optional[str]) -> list[tuple[str, str, dict]]:
    """`(slug, ref, vault row)` for each key of `sub`'s tenant — empty for a
    bare or anonymous account."""
    slug = tenant_vault.rung_tenant(sub)
    if slug is None:
        return []
    out = []
    for row in credentials_store.list_credentials(credentials_store.TENANT, slug):
        ref = instance_refs.make_tenant_ref(slug, row["connector"], row.get("account") or "")
        out.append((slug, ref, row))
    return out
