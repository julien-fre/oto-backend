"""Nextmotion — the client, the argument guards, the translation of upstream errors and
the probe, shared by all the connector's tool modules.

Split from the tool modules to stay under 500 lines. Three rules live here:
- **no argument is silently dropped**: "provided" reads as `is not None`, never
  truthiness — `dry_run=False` and `offset=0` are provided values;
- **an upstream error is classified on `status_code`**, never on the message text;
- **a write is a preview unless told otherwise**: `dry_run` is True
  by default (`_serve_write`), the preview validates `data` against the input
  allowlist (`nextmotion_entrees`), re-reads the targeted object and says what would go out, without
  calling any write method. `Kind`, `Write` and what serves them
  (`_serve_kind`, `_serve`, `_serve_write`, `_check_body`) are at the end of the module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

if TYPE_CHECKING:
    from oto.tools.nextmotion import NextmotionClient

_NAME = "nextmotion"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _client() -> NextmotionClient:
    """The client for THIS caller's key. The real import is inside the body: tests
    replace the package's class."""
    from oto.tools.nextmotion import NextmotionClient

    key, _ = access.resolve_api_key(_NAME)
    if not isinstance(key, str) or not key.strip():
        # If empty, the client would look for a key in the server's environment.
        raise _bad("Nextmotion: no API key set for this connector.")
    return NextmotionClient(api_key=key.strip())


def _run(fn: Callable[[], Any]) -> Any:
    """Runs a client call; a validation or upstream error becomes an
    `INVALID_PARAMS` instruction."""
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        raise _bad(_upstream_message(e)) from None


def _need(op: str, **required: Any) -> None:
    missing = [n for n, v in required.items() if v is None or v == ""]
    if missing:
        raise _bad(f"op={op!r} requires {', '.join('`' + m + '`' for m in missing)}.")


def _refuse_ignored(op: str, **provided: Any) -> None:
    """An argument provided that THIS op does not use is an error of intent.
    `is not None`, never truthiness: `False` and `0` are provided values."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}`.")


def _paging(limit: Optional[int], offset: Optional[int]) -> dict:
    return {"limit": 50 if limit is None else limit, "offset": 0 if offset is None else offset}


def _error_codes(body: Any) -> set:
    if not isinstance(body, dict):
        return set()
    return {e.get("code") for e in body.get("errors") or [] if isinstance(e, dict)}


def _upstream_message(e: Any) -> str:
    status = e.status_code
    if status == 401:
        return "Nextmotion: API key rejected (HTTP 401) — invalid, regenerated or deleted."
    if status == 403:
        if "non_employee_access_denied" in _error_codes(e.body):
            return ("Nextmotion: access denied (HTTP 403) — the key's user "
                    "is not an employee of this clinic.")
        return "Nextmotion: access denied (HTTP 403)."
    if status == 404:
        return "Nextmotion: resource not found (HTTP 404)."
    if status == 429:
        return "Nextmotion: too many requests (HTTP 429) — retry in a moment."
    if status >= 500:
        return f"Nextmotion is temporarily unavailable (HTTP {status})."
    return f"Nextmotion rejected the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe: `GET /v4/users/me`, no parameter and no effect.

    An empty key is rejected BEFORE the client: passed empty, `NextmotionClient`
    would resolve `NEXTMOTION_API_KEY` in the server's environment and test
    a different key than the one set."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.nextmotion import NextmotionClient

    key = (fields or {}).get("key")
    if not isinstance(key, str) or not key.strip():
        raise connector_verify.NonAutorise("Nextmotion: empty API key.")
    try:
        NextmotionClient(api_key=key.strip()).get_me()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(_upstream_message(e)) from e
        raise


_SAME = "__kind__"  # `Write.withheld` default: the kind's
_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _uuid(op: str, name: str, value: str) -> None:
    """A path id is a UUID — checked BEFORE the preview, as the client would do
    at write time: a preview never promises what the write would refuse."""
    if not _UUID.fullmatch(str(value)):
        raise _bad(f"op={op!r}: `{name}` must be a UUID — got {value!r}.")


@dataclass(frozen=True)
class Write:
    """A write served under an `op`: its target, its body, its call.

    - `call(c, cible, corps)` — the client call; `cible` is the `clinic_id`, the object's id
      or `None` depending on `target`, `corps` is `None` when the op sends nothing;
    - `target` — "clinic" (creation under a clinic), "item" (the targeted object) or "none";
    - `accepted` — the INPUT allowlist (`nextmotion_entrees`); `None` = no
      body, and a provided `data` is rejected;
    - `required` — the top-level fields the spec requires;
    - `many` — the body is a LIST of objects, and the response a page;
    - `optional_body` — an omitted `data` sends an empty body;
    - `current(c, cible, corps)` — the state shown by the preview, already projected (`None` = the
      kind's single read when the target is the object);
    - `defaults` — values set when the caller does not give them: the notification
      flags the spec sets to `true` go out as `false`, never an implicit
      notification; `notify` names these flags, and the preview says which would notify;
    - `key` / `shape` / `withheld` — the response projection when it is not the kind's
      (a conversion returns a patient, a distribution a page…)."""
    call: Callable[..., Any]
    target: str = "item"
    accepted: Optional[tuple] = None
    required: tuple = ()
    many: bool = False
    optional_body: bool = False
    current: Optional[Callable[..., Any]] = None
    defaults: dict = field(default_factory=dict)
    notify: tuple = ()
    key: Optional[str] = None
    shape: Optional[Callable[[Any], Any]] = None
    withheld: Optional[str] = _SAME


@dataclass(frozen=True)
class Kind:
    """A resource served under a tool `kind`: its allowlist, its calls, its own
    filters, its writes. `lister(c, clinic_id, **filtres, limit, offset)` and
    `lire(c, id)` are `None` when the API has no such endpoint (the op is then rejected,
    by name); `writes` maps a write op to its `Write`."""
    plural: str
    shape: Callable[[Any], Any]
    lister: Optional[Callable[..., Any]] = None
    lire: Optional[Callable[..., Any]] = None
    filters: tuple = ()
    withheld: Optional[str] = None
    writes: Optional[dict] = None


def _serve_kind(kinds: dict, kind: str, op: str, *, client: Callable[[], Any],
                clinic_id: Optional[str],
                item_id: Optional[str], filters: dict, limit: Optional[int],
                offset: Optional[int], fields: Optional[list],
                item_name: str = "item_id") -> dict:
    """`op` list | get of a `kind` tool: a filter of another kind is rejected, and so is an
    argument the op does not use (`is not None`); the call goes out afterwards.

    `client` is the factory (`_client`), passed by the tool module: that is where
    the version-skew probe reads the methods called."""
    from .nextmotion_socle import _one, _page

    if kind not in kinds:
        raise _bad(f"unknown kind: {kind!r}.")
    k = kinds[kind]
    for name, value in filters.items():
        if name not in k.filters and value is not None:
            raise _bad(f"kind={kind!r} does not use `{name}`.")
    own = {name: filters[name] for name in k.filters}
    if op == "list":
        if k.lister is None:
            raise _bad(f"kind={kind!r}: the Nextmotion API has no list — op='get'.")
        _need(op, clinic_id=clinic_id)
        _refuse_ignored(op, **{item_name: item_id})
        c = client()
        return _page(_run(lambda: k.lister(c, clinic_id, **own, **_paging(limit, offset))),
                     k.plural, k.shape, fields=fields, withheld=k.withheld)
    if op == "get":
        if k.lire is None:
            raise _bad(f"kind={kind!r}: the Nextmotion API has no single read — "
                       "op='list'.")
        _need(op, **{item_name: item_id})
        _refuse_ignored(op, clinic_id=clinic_id, limit=limit, offset=offset, fields=fields,
                        **own)
        c = client()
        return _one(_run(lambda: k.lire(c, item_id)), kind, k.shape, withheld=k.withheld)
    raise _bad("op must be 'list' or 'get'.")


def _crud(create: Optional[Callable[..., Any]], update: Optional[Callable[..., Any]],
          delete: Optional[Callable[..., Any]], accepted: tuple, required: tuple = (), *,
          accepted_update: Optional[tuple] = None,
          required_update: Optional[tuple] = None) -> dict:
    """The usual writes of a clinic resource: `create` under the clinic,
    `update` and `delete` on the object. A `None` call = the API has no such endpoint."""
    writes = {}
    if create is not None:
        writes["create"] = Write(create, "clinic", accepted, required)
    if update is not None:
        writes["update"] = Write(
            update, "item", accepted if accepted_update is None else accepted_update,
            required if required_update is None else required_update)
    if delete is not None:
        writes["delete"] = Write(delete, "item")
    return writes


def _serve(kinds: dict, kind: str, op: str, *, client: Callable[[], Any],
           clinic_id: Optional[str], item_id: Optional[str], filters: dict,
           limit: Optional[int], offset: Optional[int], fields: Optional[list],
           data: Any, dry_run: Optional[bool], item_name: str = "item_id") -> dict:
    """A `kind` tool that reads AND writes: list | get through `_serve_kind`, any other op
    through `_serve_write`. An argument of the other family is rejected (`is not None`)."""
    if kind not in kinds:
        raise _bad(f"unknown kind: {kind!r}.")
    if op in ("list", "get"):
        _refuse_ignored(op, data=data, dry_run=dry_run)
        return _serve_kind(kinds, kind, op, client=client, clinic_id=clinic_id,
                           item_id=item_id, filters=filters, limit=limit, offset=offset,
                           fields=fields, item_name=item_name)
    return _serve_write(kinds, kind, op, client=client, clinic_id=clinic_id,
                        item_id=item_id, data=data, dry_run=dry_run,
                        unused={**filters, "limit": limit, "offset": offset,
                                "fields": fields}, item_name=item_name)


def _serve_write(kinds: dict, kind: str, op: str, *, client: Callable[[], Any],
                 clinic_id: Optional[str], item_id: Optional[str], data: Any,
                 dry_run: Optional[bool], unused: dict,
                 item_name: str = "item_id") -> dict:
    """A write: target and body validated BEFORE any call; `dry_run` (True by
    default) returns the preview without calling any write method; the response goes back
    through the resource's allowlist. `item_name` = the name of the id argument in
    the tool (`quote_id`…), so that rejections name it."""
    from .nextmotion_socle import _one, _page

    k = kinds[kind]
    w = (k.writes or {}).get(op)
    if w is None:
        ops = ", ".join(["list", "get", *(k.writes or {})])
        raise _bad(f"kind={kind!r}: unknown op={op!r} — ops: {ops}.")
    _refuse_ignored(op, **unused)
    if w.target == "clinic":
        _need(op, clinic_id=clinic_id)
        _refuse_ignored(op, **{item_name: item_id})
        target, target_name = clinic_id, "clinic_id"
    elif w.target == "item":
        _need(op, **{item_name: item_id})
        _refuse_ignored(op, clinic_id=clinic_id)
        target, target_name = item_id, item_name
    else:
        _refuse_ignored(op, clinic_id=clinic_id, **{item_name: item_id})
        target, target_name = None, None
    if target is not None:
        _uuid(op, target_name, target)
    body = _check_body(kind, op, w, data)
    if w.defaults and isinstance(body, dict):
        body = {**w.defaults, **body}
    c = client()
    if dry_run is None or dry_run:
        preview: dict = {"dry_run": True, "would": op, "kind": kind}
        if target_name:
            preview[target_name] = target
        current = w.current
        if current is None and w.target == "item" and k.lire is not None:
            def current(cl, i, _):
                return _one(_run(lambda: k.lire(cl, i)), kind, k.shape, withheld=k.withheld)
        if current is not None:
            preview.update(current(c, target, body))
        if body is not None:
            preview["data"] = _mask(body)
        if w.notify:
            preview["notifie_le_patient"] = [f for f in w.notify if (body or {}).get(f)]
        preview["note"] = f"Nothing is written. Call again with dry_run=False to {op}."
        return preview
    env = _run(lambda: w.call(c, target, body))
    key = w.key or (k.plural if w.many else kind)
    shape = w.shape or k.shape
    withheld = k.withheld if w.withheld == _SAME else w.withheld
    if env is None:  # 204
        ident = {target_name: target} if target_name else {}
        return {"deleted": True, **ident} if op == "delete" else {"done": True, "op": op,
                                                                  **ident}
    if w.many:
        return _page(env, key, shape, withheld=withheld)
    return _one(env, key, shape, withheld=withheld)


def _check_body(kind: str, op: str, w: Write, data: Any) -> Any:
    """`data` against the op's input allowlist: a field the list does not name
    is REJECTED, by name, at any depth — never ignored."""
    if w.accepted is None:
        _refuse_ignored(op, data=data)
        return None
    if data is None:
        if w.optional_body:
            return None
        raise _bad(f"op={op!r} requires `data` (kind={kind!r}).")
    if w.many:
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise _bad(f"op={op!r}: `data` must be a list of objects.")
        for i, row in enumerate(data):
            _check_fields(kind, op, row, w.accepted, f"data[{i}]")
            _check_required(op, row, w.required, f"data[{i}]")
        return data
    if not isinstance(data, dict):
        raise _bad(f"op={op!r}: `data` must be an object.")
    _check_fields(kind, op, data, w.accepted, "data")
    _check_required(op, data, w.required, "data")
    return data


def _check_fields(kind: str, op: str, obj: dict, accepted: tuple, path: str) -> None:
    names = {f if isinstance(f, str) else f[0] for f in accepted}
    subs = {f[0]: f[1] for f in accepted if not isinstance(f, str)}
    unknown = sorted(set(obj) - names)
    if unknown:
        raise _bad(f"`{path}`: field(s) not accepted by kind={kind!r} op={op!r}: "
                   f"{', '.join('`' + u + '`' for u in unknown)}. Accepted: "
                   f"{', '.join(sorted(names))}.")
    for name, sub in subs.items():
        value = obj.get(name)
        rows = value if isinstance(value, list) else [value]
        for i, row in enumerate(rows):
            if isinstance(row, dict):
                where = f"{path}.{name}" + (f"[{i}]" if isinstance(value, list) else "")
                _check_fields(kind, op, row, sub, where)


def _check_required(op: str, obj: dict, required: tuple, path: str) -> None:
    missing = [r for r in required if obj.get(r) is None or obj.get(r) == ""]
    if missing:
        raise _bad(f"op={op!r}: `{path}` requires {', '.join('`' + m + '`' for m in missing)}.")


def _mask(body: Any) -> Any:
    """The preview restates the body, except fields that carry a secret (`headers`)."""
    from .nextmotion_entrees import _SECRETS

    if isinstance(body, list):
        return [_mask(r) for r in body]
    if not isinstance(body, dict):
        return body
    return {k: ("<masked>" if k in _SECRETS else v) for k, v in body.items()}
