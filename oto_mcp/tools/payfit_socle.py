"""PayFit — the output ENVELOPES and the one key this connector renames.

Separate from `payfit.py` and its siblings so that a single part decides the SHAPE
of what goes out: pagination, the `fields` projection, and the fate of the absence type.

## What changed on 17/09/2026, and why

This module used to carry an **allowlist**: only a few named fields went out of
the company, the collaborator, the contract, the absence. The removal was HARD-CODED —
nobody could open it, not even the company that owns its own
payroll data. Alexis's decision (usage signal #1063): **we serve everything the
API exposes**, and protection now goes through **per-org field filters**
(ADR 0009/0015), with **protective server defaults** set in
`field_filter_defaults.SERVER_DEFAULTS["payfit"]` — which an org_admin can lift,
connector by connector.

Direct consequence: there is no more `_pick`. A response goes out as the API
delivers it, and what reduces it is (1) the PayFit key's scope, (2) the org's
redaction policy, (3) `fields` when the caller wants fewer tokens.

## The renamed key, and why it is necessary here

⚠️ **`FieldFilter` matches by LEAF KEY NAME, at any depth.** A rule
on `type` would therefore ALSO hit `emails[].type`, `phoneNumbers[].type`,
`addresses[].type`, `analyticCodes[].type` and `documents[].type` — five
harmless fields corrupted to protect one. The mechanism cannot say "`type`, but
only under `absences`": that is its limit, not a configuration oversight.

An absence's type is therefore served under **`absence_type`**, a leaf name that
belongs only to it — and it is THAT name the server default masks. The upstream
`type` key is not served: two names for the same data, one filtered and the other
not, would be a sieve.

`absence_category` comes with it, computed here: `ordinary_leave` for an ordinary
leave, `restricted` for everything else. It stays readable when `absence_type`
is masked — workload planning needs to know that a person is absent and
that it is not paid leave, without reading a medical reason. It NEVER names
health: `restricted` covers a sick leave as well as a wedding.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .. import output_projection

# ORDINARY leaves: those that say nothing about health or family life.
# Any OTHER type — sickness, work accident, maternity, sick child, bereavement,
# wedding, or a type added tomorrow — falls into `restricted`. The list is closed
# on the safe side: an unknown type is never "ordinary".
ORDINARY_ABSENCE_TYPES = frozenset({
    "fr_conges_payes", "fr_rtt", "fr_repos", "fr_sans_solde", "fr_teletravail",
    "fr_ecole", "fr_absence_remuneree", "uk_annual_leave", "uk_paid_leave",
    "uk_unpaid_leave", "uk_remote", "es_vacaciones", "es_teletrabajo",
    "es_compensacion_dias_trabajados",
})
ORDINARY = "ordinary_leave"
RESTRICTED = "restricted"


def absence(a: Any) -> Any:
    """An absence, as the API delivers it, except `type` → `absence_type` +
    `absence_category` (see the module docstring)."""
    if not isinstance(a, dict):
        return a
    out = {k: v for k, v in a.items() if k != "type"}
    if "type" in a:
        out["absence_type"] = a["type"]
        out["absence_category"] = (
            ORDINARY if a["type"] in ORDINARY_ABSENCE_TYPES else RESTRICTED)
    return out


def page(env: Any, key: str, id_key: str, *, fields: Optional[list] = None,
         shape: Optional[Callable[[Any], Any]] = None,
         redaction: Optional[str] = None) -> dict:
    """A list page: `{count, next_cursor, <key>: [...]}`.

    `fields` can only REMOVE, and `id_key` is always kept: it is a
    token saving, never a read privilege — there is nothing left to open
    through this path, everything is already served. `["*"]` returns the full view.
    """
    env = env if isinstance(env, dict) else {}
    meta = env.get("meta") if isinstance(env.get("meta"), dict) else {}
    rows = env.get(key) or []
    if shape is not None:
        rows = [shape(r) for r in rows]
    out = {"count": meta.get("count"), "next_cursor": meta.get("nextPageToken") or None,
           key: rows}
    if fields is not None and output_projection.RAW not in fields:
        out = output_projection.project(out, items_path=key,
                                        fields=set(fields) | {id_key})
    if redaction:
        out["redaction"] = redaction
    return out


def rows(items: Any, key: str, id_key: str, *, fields: Optional[list] = None,
         shape: Optional[Callable[[Any], Any]] = None,
         redaction: Optional[str] = None) -> dict:
    """An UNPAGINATED list served under `key` — the API has several (accounting
    entries, health-insurance contracts, documents). Same projection, no cursor:
    inventing a `next_cursor: null` would suggest a pagination that does not exist.
    """
    items = items if isinstance(items, list) else []
    if shape is not None:
        items = [shape(r) for r in items]
    out = {"count": len(items), key: items}
    if fields is not None and output_projection.RAW not in fields:
        out = output_projection.project(out, items_path=key,
                                        fields=set(fields) | {id_key})
    if redaction:
        out["redaction"] = redaction
    return out


def one(obj: Any, key: str, *, shape: Optional[Callable[[Any], Any]] = None,
        redaction: Optional[str] = None) -> dict:
    out = {key: shape(obj) if shape is not None else obj}
    if redaction:
        out["redaction"] = redaction
    return out
