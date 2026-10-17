"""`http` connector, third module: the CALLS of a custom connector.

- `http_call` — one of its saved actions, by name. Without `_account`, the action is
  looked up across the org's custom connectors: a name that only one of them carries
  resolves alone, a shared name is refused with the candidates.
- `http_request` — a raw call, any verb, for what no action describes yet.

Both go through the same client as `http_get` (vault, egress guard, redirect refusal,
upstream-error translation), with the definition's constant headers; `http_call`
also fills an omitted argument from the definition's saved variables. Every verb is
carried: oto-core's `HttpConnectorClient.request` takes GET/POST/PUT/PATCH/DELETE.
"""
from __future__ import annotations

from typing import Optional

import requests
from fastmcp import FastMCP

from .. import custom_ops, session_org
from ..connectors import http_operations as store
from . import http as http_tool
from .http_ops import _bad, _definition, _org_connectors, _resolved, _sub


# A non-JSON success body, returned as text up to this size.
_TEXTE_MAX = 2000


def _account_for(sub: str, operation: str) -> Optional[str]:
    """The account whose definition carries `operation`, when `_account` is not
    given — None to fall back on the call's default resolution."""
    if session_org.current_call_account() is not None:
        return None
    porteurs = [c for c in _org_connectors(sub)
                if custom_ops.find(c["operations"], operation)]
    if len(porteurs) > 1:
        raise _bad(f"action `{operation}` exists on several custom connectors: "
                   + ", ".join(f"`{c['account']}` ({c['label']})" for c in porteurs)
                   + " — pass `_account=` to choose.")
    return porteurs[0]["account"] if porteurs else None


def _send(rc, headers: dict, plan: dict):
    client = http_tool.client_from_fields(rc.fields, headers)
    try:
        return client.request(plan["method"], plan["path"], params=plan["query"] or None,
                              json=plan["body"])
    except requests.JSONDecodeError as e:
        # A 2xx whose body is not JSON (`"Pet deleted"` under a JSON content type,
        # seen live): the call SUCCEEDED upstream — said as such, never as a
        # failure, or the agent retries a write that already went through.
        texte = e.doc or ""
        return {"ok": True, "non_json_body": texte[:_TEXTE_MAX],
                "truncated": len(texte) > _TEXTE_MAX}
    except ValueError as e:
        raise http_tool._value_error(e)
    except requests.HTTPError as e:
        raise http_tool._upstream_error(e)


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="http_call")
    def http_call(operation: str, args: Optional[dict] = None,
                  dry_run: bool = False) -> dict:
        """Call an action of a custom connector by name (list them with
        `http_operations`). `_account` is optional when only one of the org's custom
        connectors has this action. Arguments are checked against its input; an
        omitted one takes the connector's saved variable of the same name. Any verb.
        `dry_run=true` returns the request without sending it.

        Args:
            operation: name of the action.
            args: its arguments, by name.
            dry_run: validate and return {method, path, query, body} without calling.
        """
        sub = _sub()
        rc = _resolved(_account_for(sub, operation))
        d = _definition(rc)
        found = custom_ops.find(d["operations"], operation)
        if found is None:
            names = ", ".join(o["name"] for o in d["operations"][:30]) or "none yet"
            raise _bad(f"no action `{operation}` on custom connector `{rc.account}` — "
                       f"available: {names}.")
        try:
            plan = custom_ops.plan_call(found, args, d["variables"])
        except custom_ops.CallRefused as e:
            raise _bad(str(e))
        if dry_run:
            return {"dry_run": True, "account": rc.account, **plan}
        return _send(rc, d["headers"], plan)

    @mcp.tool(name="http_request")
    def http_request(method: str, path: str, params: Optional[dict] = None,
                     body: Optional[dict | list] = None, dry_run: bool = False) -> dict:
        """Raw call to the custom connector of this call (`_account` when there are
        several), any verb — for what no saved action describes. Its auth and
        constant headers are applied. Prefer `http_call` when an action exists.

        Args:
            method: GET | POST | PUT | PATCH | DELETE.
            path: relative to the connector's base URL, starts with /, no query.
            params: query parameters.
            body: JSON body.
            dry_run: return {method, path, query, body} without calling.
        """
        _sub()
        verb = (method or "").strip().upper()
        if verb not in custom_ops.METHODS:
            raise _bad(f"`method` must be one of {', '.join(custom_ops.METHODS)}.")
        errs = custom_ops.path_errors("`path`", path)
        if errs:
            raise _bad("; ".join(errs))
        rc = _resolved()
        plan = {"method": verb, "path": path, "query": params or {}, "body": body}
        if dry_run:
            return {"dry_run": True, "account": rc.account, **plan}
        try:
            headers = store.load_definition(rc.entity_type, str(rc.entity_id),
                                            rc.account)["headers"]
        except store.InstanceMissing:
            headers = {}      # no saved definition: the card alone
        return _send(rc, headers, plan)
