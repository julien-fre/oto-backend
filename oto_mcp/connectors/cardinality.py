"""THE question “does this connector carry several accounts?”, and its single answer.

Three tiers, in this order — this is Alexis's ruling of 2026-08-27, in his letter:

1. the override of the context **ORG**, if it exists;
2. the **PLATFORM** override, if it exists;
3. the **CODE** default — the declared `Connector.cardinality`, otherwise derived from
   the auth descriptor (`Connector.auth_multi_account`).

**Why a module and not a property.** The registry (`providers/`) is PURE: no `oto_mcp`
import, no database. It therefore cannot know about an override. And the override cannot
live halfway: a cardinality read by the WRITE GUARD but not by RESOLUTION would accept a
second account that nobody would ever read — which is exactly the defect oto-backend#409
fixed on 27/08. Hence a single source, here, that both call.

**And a THIRD face, since 2026-09-01 (oto-backend#732): the SERVED CARD.**
`providers.public_catalog()` sets `auth.cardinality` from the registry, hence from the
CODE default — this is the key the dashboard reads to decide whether to offer a second
account. An org widened by an override therefore saw the server ACCEPT a gesture that the
screen never offered: the same half-widening as #409, taken from the other end (the row
isn't set-then-ignored, it is settable and never offered). Hence `overlay_for_org()`:
the surfaces that know a requester — hence a context org — rewrite the served key from
this same single source. **The code says what is possible, the database says what is
exposed.**

⚠️ **Zero database reads on the hot path.** The cardinality is consulted up to four times
per tool call (`access/resolve.py`), on a SINGLE-LOOP server, against a remote managed
database: one query per consultation is the failure mode documented in
`docs/event-loop-perf.md`. The overrides are therefore loaded **at boot** into a process
dictionary, and reloaded by an **explicit gesture**
(`oto_admin_connector_setting op=reload`) — exactly the pattern of the issuer registry
(`server.reload_tenant_registry`), with the same consequence, to be stated in the docs:
**the reload is PER PROCESS**. Reloading preprod does not reload prod.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .. import providers

logger = logging.getLogger(__name__)

# The `key` of this property in `connector_settings`.
KEY = "cardinality"
MONO, MULTI = "mono", "multi"

# The two values of the SERVED key `auth.cardinality` — which are NOT those of the
# database. `mono`/`multi` is what is SET in `connector_settings`; `single`/
# `multi_account` is what the registry RETURNS on the card (`_model.Connector.auth`) and
# what a front end consumes (`AuthDescriptor` contract, closed set read by a `switch` in
# oto-dashboard). The translation between the two lives HERE, once: scattered, it would
# become again the divergence this module exists to prevent.
SERVI_SINGLE, SERVI_MULTI = "single", "multi_account"

# The process's LIVE overrides: {(scope_type, scope_id, connector): value}.
# Replaced by reference SWAP (atomic under CPython) — no reader sees a half-filled
# dictionary, and so there is no lock on reads.
_OVERRIDES: dict = {}
# Loading, on the other hand, is serialized: two concurrent boots (tests, threadpool)
# must not make two queries for the same result.
_LOCK = threading.Lock()
_LOADED = False


def reload() -> int:
    """Re-reads the overrides from the database and installs them. Returns their count.

    Read failure ⟹ the exception PROPAGATES and nothing is installed: the process keeps
    the previous overrides, whole — never a half-set. It is up to the caller (the boot,
    the admin capability) to decide whether to tolerate the failure."""
    global _OVERRIDES, _LOADED
    from ..db import connector_settings as store
    neuf = {}
    for r in store.list_connector_settings(KEY):
        valeur = (r["value"] or "").strip().lower()
        if valeur not in (MONO, MULTI):
            # Counted and logged, never interpreted: inventing a meaning for an
            # unknown value is deciding in place of whoever set it.
            logger.warning(
                "cardinality: override IGNORED, unknown value %r for %s/%s %s "
                "(expected %r or %r)", r["value"], r["scope_type"], r["scope_id"],
                r["connector"], MONO, MULTI)
            continue
        neuf[(r["scope_type"], str(r["scope_id"]), r["connector"])] = valeur
    _OVERRIDES = neuf
    _LOADED = True
    logger.info("cardinality: %d override(s) loaded%s", len(neuf),
                (" — " + ", ".join(f"{k[2]}@{k[0]}:{k[1]}={v}"
                                   for k, v in sorted(neuf.items()))) if neuf else "")
    return len(neuf)


def _ensure_loaded() -> None:
    """First call: loads. **Logged fail-open** — an unreachable database leaves the
    dictionary EMPTY, so everyone falls back on the code default, which is the behavior
    from before this batch. The direction of the fallback is the point: an override
    WIDENS (mono → multi), so its absence can only tighten, never open."""
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        try:
            reload()
        except Exception:
            logger.warning("cardinality: overrides unreadable — registry defaults "
                           "only (fail-open)", exc_info=True)
            _LOADED = True


def _porteur(connector: str):
    """The registry entry of the connector that CARRIES `connector`'s credential.

    Two functions, and both are needed: `credential_provider` resolves the DELEGATION
    (the six unipile channels point to `unipile`) — it is a pure name computation, a
    single level — and `connector_for_provider` is the lookup. Without the first,
    overriding `unipile` would leave its channels on the code default: two answers for a
    single key, exactly the divergence of 2026-07-07 (green card next to a “Blocked”)."""
    return providers.connector_for_provider(providers.credential_provider(connector))


def overrides_snapshot() -> dict:
    """Copy of the live overrides — admin surface and log. Never the dict itself
    (exposing it would let a caller edit it without going through the database)."""
    _ensure_loaded()
    return dict(_OVERRIDES)


def is_multi_account(connector: str, org: "int | str | None" = None) -> bool:
    """Does this connector carry several accounts, FOR THIS ORG? The only function
    to call — the write guard and resolution both go through here.

    `connector` is normalized to the credential's CARRIER (delegation
    `Connector.credential_of`: the unipile channels point to `unipile`), as everywhere
    else — otherwise an override set on the carrier would be invisible from a channel,
    and vice versa.

    `org` = the requester's CONTEXT org, never their membership (same reading as
    `_platform_grantee_scope`). None ⟹ only the platform override applies."""
    con = _porteur(connector)
    if con is None:
        return False
    _ensure_loaded()
    if org is not None:
        valeur = _OVERRIDES.get(("org", str(org), con.name))
        if valeur:
            return valeur == MULTI
    valeur = _OVERRIDES.get(("platform", "platform", con.name))
    if valeur:
        return valeur == MULTI
    return con.auth_multi_account


def accepted_anywhere(connector: str) -> bool:
    """Does the connector accept an `_account=` at call time, in ANY one org?

    Deliberately org-AGNOSTIC, and permissive. The `_account=` axis is read very low in
    the call path (`call_axes.axes_for_call`, called by the middleware), where the
    context org would cost a query on every call. Yet the axis authorizes nothing: it
    only NAMES an account, and it is resolution that refuses, actionably, if that account
    doesn't exist at the tier. Accepting the word where the org hasn't been widened
    therefore grants access to nothing — whereas refusing it would make a widened org
    UNABLE to target its second account: the defect of oto-backend#409, a key set that
    nothing will read."""
    con = _porteur(connector)
    if con is None:
        return False
    if con.auth_multi_account:
        return True
    _ensure_loaded()
    return any(v == MULTI for (_, _, nom), v in _OVERRIDES.items() if nom == con.name)


def overlay_for_org(rows: list, org: "int | str | None") -> list:
    """Rewrites the `auth.cardinality` of catalog rows with the EFFECTIVE answer for this
    org. The only way to serve the card to a known requester.

    `rows` = rows of `providers.public_catalog()`, whose `auth.cardinality` carries the
    CODE default (the registry is pure, it cannot read an override). This function is the
    bridge: it runs each name back through `is_multi_account`, hence through the three
    tiers org > platform > code.

    **Copy on write, never mutation.** A row whose verdict doesn't change is returned AS
    IS (same object); only the one that moves is copied, with its `auth` copied too. The
    producer does rebuild its dicts on every call, but a caller passing a cache must not
    see its data rewritten under it — and `public_catalog` returns `credential_fields`
    from a SECOND call to `c.auth`, so mutating in place would make two keys of the same
    row diverge.

    ⚠️ **Zero queries**, despite the warning at the top of the module: the overrides are
    already in memory (`_ensure_loaded`) and `_porteur` is only a dict lookup. The cost is
    a few lookups per catalog ROW, on a CONSULTATION surface — not on the hot path of a
    tool call, which is what that warning protects.

    `org=None` (anonymous storefront, site build) remains legitimate and means “no
    context”: the PLATFORM override still applies — it is the platform's answer for
    everyone — and only an org's is out of reach."""
    out = []
    for row in rows:
        auth = row.get("auth")
        if not isinstance(auth, dict):
            # Compact row (the catalog without `verbose`): nothing to rewrite. Returned
            # as is rather than skipped — this function filters zero rows.
            out.append(row)
            continue
        servie = SERVI_MULTI if is_multi_account(row["name"], org) else SERVI_SINGLE
        if auth.get("cardinality") == servie:
            out.append(row)
            continue
        out.append({**row, "auth": {**auth, "cardinality": servie}})
    return out


def _reset_for_tests() -> None:
    """Empties the process cache. Reserved for tests — a test that sets an override
    must be able to make it take effect without restarting the interpreter."""
    global _OVERRIDES, _LOADED
    _OVERRIDES, _LOADED = {}, False
