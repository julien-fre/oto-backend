"""HubSpot — `hubspot_object(op="update", associations=[…])`: link an EXISTING record.

No tool here: `hubspot_object` hands the `associations` of an update to `associate()`,
like `hubspot_merge` serves its `merge` op.

**Why a second request.** The v3 update (`PATCH /crm/v3/objects/{type}/{id}`) takes
`properties` only: an `associations` key in its body is ignored by HubSpot, which still
answers 200. Forwarding it there would report a link that was never made — the record
looks updated, the association is not there. Creating a link on an existing record is
its own endpoint, the v4 association PUT, one request per target:

- `PUT /crm/v4/objects/{type}/{id}/associations/{to_type}/{to_id}` with the
  association `types` as body (a labelled association), or
- `PUT /crm/v4/objects/{type}/{id}/associations/default/{to_type}/{to_id}` when no
  `types` is given (HubSpot's default, unlabelled association).

**The target type comes from `to_object_type`.** A v3 association object
(`{"to": {"id": …}, "types": […]}`, the form `op="create"` takes) does not name the
type of its target — create infers it from the association type id; the v4 path
needs it in the URL. One `to_object_type` per call, the same parameter as
`op="associations"`.

**A target never stops the batch, and nothing is reported done that was not.** Each
target gets its own verdict (`applied` / `failed` with HubSpot's reason); the
properties, when given, are written first. When EVERY association failed and no
property was written, nothing changed: the call raises instead of answering a
success. ≤ 100 targets per call; the 429 retry is the client's
(`HubSpotClient._request`).
"""
from __future__ import annotations

from typing import Any

MAX_TARGETS = 100


def parse(value: Any) -> list[tuple[str, list | None]]:
    """`associations` of an update → [(to_id, types or None)], or ValueError naming
    the expected form. `types`, when present, goes to HubSpot verbatim."""
    if not isinstance(value, list) or not value:
        raise ValueError(
            "op='update' expects associations = non-empty list of "
            "{\"to\": {\"id\": \"<target id>\"}, \"types\": [...]} (types optional: "
            "omitted = HubSpot's default association), with to_object_type naming "
            "the target type")
    if len(value) > MAX_TARGETS:
        raise ValueError(f"op='update' takes at most {MAX_TARGETS} associations per call")
    out: list[tuple[str, list | None]] = []
    for item in value:
        to = item.get("to") if isinstance(item, dict) else None
        to_id = to.get("id") if isinstance(to, dict) else None
        if to_id in (None, ""):
            raise ValueError(
                "each association needs {\"to\": {\"id\": \"<target id>\"}}; got "
                f"{item!r}")
        types = item.get("types")
        if types is not None and (not isinstance(types, list) or not types
                                  or not all(isinstance(t, dict) for t in types)):
            raise ValueError(
                "association `types` = non-empty list of {\"associationCategory\": …, "
                "\"associationTypeId\": …}, or omit it for the default association")
        out.append((str(to_id), types))
    return out


def associate(c, object_type: str, object_id: str, to_object_type: str,
              targets: list[tuple[str, list | None]]) -> dict:
    """Create each association; returns {"applied": [...], "failed": [...]}."""
    from oto.tools.common.errors import UpstreamHTTPError

    applied: list[dict] = []
    failed: list[dict] = []
    base = f"/crm/v4/objects/{object_type}/{object_id}/associations"
    for to_id, types in targets:
        if types is None:
            path, body = f"{base}/default/{to_object_type}/{to_id}", None
        else:
            path, body = f"{base}/{to_object_type}/{to_id}", types
        try:
            if body is None:
                c._request("PUT", path)
            else:
                c._request("PUT", path, json=body)
        except UpstreamHTTPError as e:
            failed.append({"to_id": to_id, "error": str(e)})
            continue
        applied.append({"to_id": to_id,
                        "types": types if types is not None else "default"})
    return {"to_object_type": to_object_type, "applied": applied, "failed": failed}
