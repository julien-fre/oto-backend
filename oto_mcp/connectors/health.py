"""SHARED helper "mark a vault row as rejected" (oto#25 lot b2).

Extracted from `capabilities/connectors/verify.py` (sole writer until now, under the
private names `_FLAGGABLE` / `_record_health`) so that modules that THEMSELVES
RECOGNIZE a dead grant (the `invalid_grant` pattern and its equivalents — today
salesforce and zoho; google on refresh stays EXCLUDED, concurrent WIP on its OAuth
return) mark the row ACTUALLY served without duplicating the gesture
or its guard. `verify.py` imports this module in place of its local definitions —
pure refactor, its behavior does not change.

Two elements:

- `FLAGGABLE_SCOPES`: the tiers whose scope does not exceed the org (or the single
  user) of the one triggering the marking. `tenant` and `platform` are ALWAYS
  excluded — shared by whole orgs (or several tenants), one caller's hiccup must not
  paint them red for everyone. `USER` (LEGACY scope `("user", sub)` from before
  ADR 0033 — no live connector has written there since the removal of MCP federation,
  2026-09-09, but rows lie DORMANT there)
  is as narrow as `MEMBER` there — a single user —
  and never reaches `verify.py` (its cascade only produces `MEMBER`/`group`/`org`:
  see `access/cascade.py`, which yields `CascadeRung("user", credentials_store.MEMBER,
  …)` — the "user" string there is a MODE, not an `entity_type`). Widening it here
  therefore changes nothing about what `verify.py` marks, and lets it cover this scope
  with the SAME guard rather than a second one.
- `record_health(provider, scope, ok, error)`: persists `meta.health_ko` +
  `meta.health_reason` (merge, best-effort). `scope=None` → no-op. This is the function
  used by `verify.py`, which handles UNMARKING itself (`ok=True` clears
  `health_ko`) — a gesture this lot (b2) does not touch (b3, to come).
- `mark_rejected(entity_type, entity_id, provider, account, error)`: the facade this
  lot adds for a module that knows its ENTITY directly (no `ResolvedCtx` nor
  pre-computed scope) — builds the scope, applies the SAME guard, never unmarks
  (always `ok=False`): marking a real rejection is never a fallback that swallows the
  error, the caller ALWAYS RE-RAISES after calling it.

- `suivre_appel(trace, quota_epuise, rejet=None)`: tracking AT CALL TIME — a
  `quota_exhausted` refusal marks the served row `no_quota`, an upstream refusal of
  the KEY itself (`error_taxonomy.credential_rejected_in_chain`) marks it
  `unauthorized` with `health_source = "call"`, and the first success clears either
  (see the "Seen at call time" section below).

Read by `connectors/readiness.py` (via `access.credential_rejection_for`, which reads
`credentials_store.credential_health`) — never the other way around, this module does
not know its readers.
"""
from __future__ import annotations

from typing import Optional

from starlette.concurrency import run_in_threadpool

from .. import credentials_store, providers

# Tiers whose key we accept to FLAG — see the module docstring above.
FLAGGABLE_SCOPES = (credentials_store.USER, credentials_store.MEMBER,
                    "group", credentials_store.ORG)

#: The verdict "the key authenticates, the account is dry" — same name as the probe's
#: (`connectors.verify.NO_QUOTA`), defined in the vault that stores and re-reads it.
NO_QUOTA = credentials_store.NO_QUOTA_VERDICT
#: The verdict "the key does not authenticate" — same name as the probe's.
UNAUTHORIZED = credentials_store.UNAUTHORIZED_VERDICT


def record_health(provider: str, scope: "tuple | None", ok: bool,
                  error: "str | None", verdict: "str | None" = None,
                  source: "str | None" = None) -> None:
    """Persists the health state of the tested credential (`meta.health_ko` + reason +
    `meta.health_verdict`) — read by `status_for` (sheet) and
    `access.credential_rejection_for`, hence by the connector card's `ready` verdict.
    Merge (overwrites nothing), best-effort. `scope` = `(entity_type,
    entity_id, account)` of the row ACTUALLY tested/served; `None` (key shared
    beyond the guard) → we don't flag. `verdict` = the failure's classification
    (`no_quota`, `unauthorized`…) when known: it is what makes the card say
    "top up" rather than "set the key again". `source` = `CALL_SOURCE` for a mark seen
    by a call (lifted by the next successful call), `None` for the probe's (lifted only
    by the probe or by re-setting the key): writing it on every mark keeps an old call
    source from outliving a newer probe verdict.

    ⚠️ The only function that UNMARKS (`ok=True` clears `health_ko`/`health_reason`/
    `health_verdict`) — `mark_rejected` below only calls it with `ok=False`."""
    if scope is None:
        return
    try:
        credentials_store.update_meta(
            scope[0], scope[1], provider, scope[2],
            {"health_ko": (not ok), "health_reason": (error if not ok else None),
             "health_verdict": (verdict if not ok else None),
             "health_source": (source if not ok else None)})
    # noqa: SILENT — declared debt: the unwritten health flag should be logged (#424, verdict C)
    except Exception:  # noqa: BLE001 — health is a bonus, never blocking
        pass


def mark_rejected(entity_type: Optional[str], entity_id: Optional[str],
                  provider: str, account: str, error: "str | None",
                  verdict: "str | None" = None) -> None:
    """Marks `health_ko` on `(entity_type, entity_id, provider, account)` — never
    on a scope outside `FLAGGABLE_SCOPES` (tenant/platform), never if `entity_id`
    is absent. For a module that ITSELF RECOGNIZES a dead grant on the row
    it knows to be the right one (never by generic deduction) — the caller ALWAYS
    RE-RAISES the original exception right after: marking is never a fallback
    that swallows the real error."""
    if entity_type not in FLAGGABLE_SCOPES or not entity_id:
        return
    record_health(provider, (entity_type, entity_id, account or ""), False, error,
                  verdict)


# --- Seen AT CALL TIME: credits exhausted (option (a), 2026-09-25), key refused -----
#
# The `oto_instance op=verify` probe already classified a 402 as `no_quota` — but
# nobody replays it before working. An agent would hit "credits exhausted" mid-work,
# the connector's card stayed green, and nobody topped up: 13 signals (theirstack,
# AI Ark…) before this lot. Now a CALL's refusal marks the key that served it, and the
# first successful call on that same key clears the mark.
#
# The same holds for a key the upstream REFUSES (401, or a refusal its connector
# declares as the key's): the probe painted it red only when someone replayed it, so
# an agent hitting it every hour left the card green and the alert (`maintenance
# alertes-credential`, which reads this mark) blind. Its mark carries
# `health_source = "call"`, so that a later successful call lifts it — and a probe
# mark (which may name a missing scope the call does not exercise) stays.
#
# Cost on the hot path: the call record already carries the served row
# (`access.resolve` → `credential_row`); clearing writes only once per key and per
# process (`_SANS_MARQUE`), conditionally (the mark must be `no_quota`), and
# outside the event loop.

#: Keys known, since this process started, to carry no `no_quota` mark (a
#: conditional clear already went through). A mark being set removes them from it.
#: Lost on restart: we then pay ONE conditional write again.
_SANS_MARQUE: set = set()


def _ligne(row) -> Optional[tuple]:
    """`(entity_type, entity_id, carrier, account)` of the served row, carrier
    normalized (a delegation stores its key under the carrier: see `verify.py`), or None
    if the row is not markable (tenant, platform, no entity)."""
    if not row:
        return None
    entity_type, entity_id, provider, account = row
    if entity_type not in FLAGGABLE_SCOPES or not entity_id or not provider:
        return None
    return (entity_type, entity_id, providers.credential_provider(provider),
            account or "")


def marquer_quota_epuise(row, message: "str | None") -> None:
    """Sync (DB): marks the served row `no_quota` — no-op outside `FLAGGABLE_SCOPES`
    (a platform or tenant key is NEVER painted red for everyone)."""
    ligne = _ligne(row)
    if ligne is None:
        return
    record_health(ligne[2], (ligne[0], ligne[1], ligne[3]), False, message, NO_QUOTA)
    _SANS_MARQUE.discard(ligne)


def marquer_rejet_a_l_appel(row, message: "str | None") -> None:
    """Sync (DB): marks the served row `unauthorized`, source `call` — no-op outside
    `FLAGGABLE_SCOPES` (a platform or tenant key is NEVER painted red for everyone)."""
    ligne = _ligne(row)
    if ligne is None:
        return
    record_health(ligne[2], (ligne[0], ligne[1], ligne[3]), False, message,
                  UNAUTHORIZED, source=credentials_store.CALL_SOURCE)
    _SANS_MARQUE.discard(ligne)


def effacer_marque_d_appel(row) -> None:
    """Sync (DB): on a SUCCESSFUL call, lifts a mark set AT CALL TIME from the served
    row (`no_quota`, or `unauthorized` from a call) — a single conditional write per
    key and per process. A probe mark is left alone (see `clear_call_health`)."""
    ligne = _ligne(row)
    if ligne is None or ligne in _SANS_MARQUE:
        return
    try:
        credentials_store.clear_call_health(*ligne)
    # noqa: SILENT — declared debt: an unwritten clear leaves the card red until the probe (#424, verdict C)
    except Exception:  # noqa: BLE001 — health is a bonus, never blocking
        return
    _SANS_MARQUE.add(ligne)


def _a_effacer(row) -> bool:
    ligne = _ligne(row)
    return ligne is not None and ligne not in _SANS_MARQUE


async def suivre_appel(trace: Optional[dict], quota_epuise: "str | None",
                       rejet: "str | None" = None) -> None:
    """After a tool call: `quota_epuise` = the message of the `quota_exhausted` refusal
    (the served key is marked `no_quota`), `rejet` = the message of a refusal of the
    KEY (marked `unauthorized`, source `call`), both `None` = success (a mark set at
    call time on the served key is lifted). Outside the loop, best-effort: health
    tracking must never change the result of the call it observes."""
    row = (trace or {}).get("credential_row")
    if row is None:
        return
    if quota_epuise is not None:
        await run_in_threadpool(marquer_quota_epuise, row, quota_epuise)
    elif rejet is not None:
        await run_in_threadpool(marquer_rejet_a_l_appel, row, rejet)
    elif _a_effacer(row):
        await run_in_threadpool(effacer_marque_d_appel, row)
