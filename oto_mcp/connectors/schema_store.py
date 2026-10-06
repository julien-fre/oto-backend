"""OBSERVED schema of connectors — derived from real responses (keys+types skeleton,
NEVER values/PII).

Why observe rather than declare: connector outputs are **passthroughs** of
third-party APIs that we do not own (Unipile, ATS, Apollo…); a hand-written schema
drifts. The right schema = what actually flows through. We extract the **redactable
leaves** (scalars + lists of scalars) with their path(s) and a type — and
persist per service (namespace), with incremental merging. Process cache so as not to
write to the database on every call. Best-effort: NEVER break a tool call.

`name` may appear at several paths (e.g. `skills[].name`, `languages[].name`) —
we keep the set of paths to make the ambiguity VISIBLE in the UI (a toggle on
the `name` key affects all those paths).
"""
from __future__ import annotations

import threading
from typing import Any

from .. import db

_SCALAR_TYPE = {bool: "boolean", int: "number", float: "number", str: "string", type(None): "null"}

# service -> {name: {"type": str, "paths": set[str]}}
_cache: dict[str, dict[str, dict]] = {}
_lock = threading.Lock()

# Anti-pile-up safeguards. The schema normally converges (named keys, arrays
# collapsed to `[]`), but a response with DYNAMIC KEYS (map keyed by id) would make it
# explode: beyond the cap we no longer add new keys / new paths.
_MAX_KEYS = 1000
_MAX_PATHS_PER_KEY = 50


def _type_of(v: Any) -> str:
    return _SCALAR_TYPE.get(type(v), "string")


def _scalar(v: Any) -> bool:
    return not isinstance(v, (dict, list))


def leaves(payload: Any, path: str = "", out: dict[str, dict] | None = None) -> dict[str, dict]:
    """Observed redactable leaves: `{name: {"type", "paths": set}}`.

    A leaf = a key whose value is a scalar OR a list of scalars
    (`emails: [...]`). Dicts / lists of dicts are traversed in depth without
    being listed (structure, not leaf)."""
    if out is None:
        out = {}
    if isinstance(payload, dict):
        for k, v in payload.items():
            if not isinstance(k, str):
                continue
            p = f"{path}.{k}" if path else k
            if _scalar(v):
                e = out.setdefault(k, {"type": _type_of(v), "paths": set()})
                e["paths"].add(p)
            elif isinstance(v, list):
                if all(_scalar(x) for x in v):
                    e = out.setdefault(k, {"type": _type_of(v[0]) if v else "string", "paths": set()})
                    e["paths"].add(p + "[]")
                else:
                    leaves(v, p + "[]", out)
            elif isinstance(v, dict):
                leaves(v, p, out)
    elif isinstance(payload, list):
        for x in payload:
            leaves(x, path, out)
    return out


def observe(service: str, payload: Any) -> None:
    """Merges the skeleton of `payload` into the service's persisted schema.
    Best-effort, never blocking: any error is swallowed."""
    try:
        found = leaves(payload)
        if not found:
            return
        with _lock:
            cur = _cache.get(service)
            if cur is None:
                cur = _load(service)
                _cache[service] = cur
            if _merge(cur, found):
                db.upsert_connector_schema(service, _serialize(cur))
    # noqa: SILENT — schema observation is optional, never blocking
    except Exception:
        pass


def _load(service: str) -> dict[str, dict]:
    raw = db.get_connector_schema(service) or {}
    return {n: {"type": i.get("type", "string"), "paths": set(i.get("paths", []))} for n, i in raw.items()}


def _merge(cur: dict[str, dict], found: dict[str, dict]) -> bool:
    changed = False
    for name, info in found.items():
        e = cur.get(name)
        if e is None:
            if len(cur) >= _MAX_KEYS:
                continue  # cap: no new key is added (response with dynamic keys)
            cur[name] = {"type": info["type"], "paths": set(list(info["paths"])[:_MAX_PATHS_PER_KEY])}
            changed = True
        else:
            if len(e["paths"]) >= _MAX_PATHS_PER_KEY:
                continue
            n = len(e["paths"])
            for p in info["paths"]:
                if len(e["paths"]) >= _MAX_PATHS_PER_KEY:
                    break
                e["paths"].add(p)
            if len(e["paths"]) != n:
                changed = True
    return changed


def _serialize(cur: dict[str, dict]) -> dict:
    return {n: {"type": i["type"], "paths": sorted(i["paths"])} for n, i in cur.items()}


def as_fields(raw: dict) -> list[dict]:
    """Converts an observed schema (`{name: {type, paths}}`) into fields for the UI:
    `[{name, label, type}]`. `label` = the paths (shows where the key appears, e.g.
    `skills[].name · languages[].name`)."""
    out = []
    for name in sorted(raw):
        info = raw[name] or {}
        paths = info.get("paths") or []
        label = " · ".join(paths) if paths and paths != [name] else None
        out.append({"name": name, "label": label, "type": info.get("type", "string")})
    return out
