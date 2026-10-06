"""Shared description of the five-rung scale (oto-backend#775), placed in a NEUTRAL
module rather than imported from one capability into another: an import
`verify.py -> instances.py` would couple their IMPORT order, hence their
REGISTRATION order in `CAPABILITIES` (each module registers itself by decorator at
import), hence the order of the REST route table — the ratchet
`tests/api/test_api_routes_table_frozen.py`. This module defines NO capability:
it cannot shift anything.

`tenant` is the highest-level account isolated AT THE PROVIDER — hence ABOVE the
organization reading, not below. It only concerns those who reach oto through a
partner that serves it under its own brand ("host" in the public docs, same
notion): that partner can set shared keys for all the organizations it hosts.
Nothing to rename (see the 06/09/2026 correction on the issue) — only the
definition was missing.

⚠️ **TWO texts, because the rungs are not spelled the same on both sides.**
The nearest rung is called `member` on a `level` (`ConnectorInstance`,
`VerifyResult`, the `list` filter) and `user` on an `InstanceOwner.type` — same
rung, two spellings served (`instances.py`: `level='member'` is set with
`owner={'type': 'user'}`). A single text for both would list a value that does
not exist where it was served, and leave the other undefined. The definition of
`tenant`, on the other hand, stays SINGLE (`DOC_TENANT`): that is the one we do
not want to see diverge.
"""
from __future__ import annotations

DOC_TENANT = (
    "`tenant` is the HIGHEST-LEVEL account, isolated at the provider — "
    "above the organization reading, not below: it only concerns accounts that "
    "reach oto through a partner that serves it under its own brand "
    "(called \"host\" in the public docs, same notion), and that can set shared "
    "keys for all the organizations it hosts."
)

# Served on the `level` fields — whose enumeration opens with `member`.
DOC_LEVEL = (
    "Proximity rank in the resolution cascade: `member` (the caller's own key) "
    "< `group` (their team) < `org` (their organization) < "
    "`tenant` < `platform` (default oto key). " + DOC_TENANT
)

# Served on `InstanceOwner.type` — same scale, but the nearest rung is written
# `user` there (the person), never `member`.
DOC_OWNER_TYPE = (
    "Tier that OWNS the instance, along the same resolution cascade: "
    "`user` (the person themself, who carries a sub) < `group` (a team) < "
    "`org` (an organization) < `tenant` < `platform` (default oto key, without "
    "an id: it is identified by its label, ADR 0044 §F). " + DOC_TENANT
)
