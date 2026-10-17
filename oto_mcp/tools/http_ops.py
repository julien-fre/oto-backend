"""`http` connector, second module: setting up a Custom API, and its described operations.

`tools/http.py` reaches a configured API by raw path (`http_get`/`http_post`). This
module adds what makes the connector usable for a tool we do not ship:

- `http_setup_link` — the link a person opens to connect the API THEMSELVES, the key
  typed in the form. A raw secret is never an MCP argument (CLAUDE.md): the tool has
  no parameter that could carry one.
- `http_operations` / `http_set_operations` — the operations an org DESCRIBES in data
  (`custom_ops`), stored on the credential's instance (`connectors/http_operations`).
- `http_call` — call one of them by name: arguments checked, path parameters encoded
  segment by segment, then the same client as `http_get` (vault, egress guard,
  redirect refusal, upstream-error translation).

The row whose operations are read or written is the one the cascade RESOLVES for the
call (`_account`, team, org) — the same key `http_call` will use.
"""
from __future__ import annotations

import re
from typing import Literal, Optional
from urllib.parse import urlsplit

import requests
from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, custom_ops, egress, links, roles
from ..auth.hooks import current_user_sub_from_token
from ..connectors import http_operations as store
from ..mcp_errors import McpError
from ..providers.http import AUTH_MODES
from . import http as http_tool
from .lecture import LECTURE

_HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_PARAM_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,64}$")

INSTRUCTIONS = (
    "Send this link to the person who holds the API key. They open it, check the "
    "pre-filled fields and paste the key IN THE FORM: oto keeps it encrypted in the "
    "vault. Never ask for the key in the conversation and never pass it to a tool. "
    "If a key was already pasted in a chat, ask them to revoke it at the provider and "
    "create a new one before connecting. Once saved, the API is reachable with "
    "`http_get`/`http_post`, and by name with `http_call` once operations are described.")


def _bad(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def setup_params(label: str, base_url: Optional[str], auth_mode: Optional[str],
                 header_name: Optional[str], query_param: Optional[str],
                 doc_path: Optional[str]) -> dict:
    """The NON-secret pre-fill of the card, validated — split out to stay testable
    without an MCP context."""
    label = (label or "").strip()
    if not label or len(label) > 80:
        raise _bad("`label` is required (the tool's name, 80 characters max).")
    out = {"label": label}
    if auth_mode:
        if auth_mode not in AUTH_MODES:
            raise _bad(f"`auth_mode` must be one of {', '.join(AUTH_MODES)}.")
        out["auth_mode"] = auth_mode
    if base_url:
        p = urlsplit(base_url.strip())
        if p.username or p.password or p.query or p.fragment:
            raise _bad("`base_url` is the API root only: no user, no `?query`, no `#` — "
                       "a key never travels in a link; it is typed in the form.")
        try:
            egress.check_url(base_url.strip(), connector="http", field="base_url")
        except egress.EgressRefused as e:
            raise _bad(str(e))
        out["base_url"] = base_url.strip().rstrip("/")
    if header_name:
        if not _HEADER_RE.match(header_name):
            raise _bad("`header_name` is a header NAME (e.g. x-api-key), not its value.")
        out["header_name"] = header_name
    if query_param:
        if not _PARAM_RE.match(query_param):
            raise _bad("`query_param` is a parameter NAME (e.g. api_key), not its value.")
        out["query_param"] = query_param
    if doc_path:
        if not doc_path.startswith("/") or any(c in doc_path for c in "?# "):
            raise _bad("`doc_path` is a path relative to base_url (e.g. /openapi.json).")
        out["doc_path"] = doc_path
    return out


def _sub() -> str:
    sub = current_user_sub_from_token()
    if sub is None:
        raise _bad("http connector unavailable in local stdio (org credential required).")
    return sub


def _resolved():
    """The vault row the cascade serves for this call — `_account`, team, org."""
    rc = access.resolve_credential("http", want="byo")
    if rc.entity_type is None:
        raise _bad("http connector: no Custom API account of yours, your team's or your "
                   "org's resolves — connect one first (`http_setup_link`).")
    return rc


def _load(rc) -> list[dict]:
    try:
        return store.load(rc.entity_type, str(rc.entity_id), rc.account)
    except store.InstanceMissing as e:
        raise _bad(f"http connector: {e}.")


def _require_admin(sub: str, rc) -> None:
    """Same right as setting the credential itself: org admin for an org account,
    team lead for a team account, the person for their own."""
    et, eid = rc.entity_type, str(rc.entity_id)
    if et == "org":
        ok = roles.is_org_admin(sub, int(eid))
    elif et == "group":
        ok = roles.can_admin_group(sub, int(eid))
    elif et == "member":
        ok = eid.split(":", 1)[-1] == sub
    else:
        ok = et == "user" and eid == sub
    if not ok:
        raise _bad(f"Describing operations of this Custom API account is reserved to "
                   f"whoever administers it (the {et} admin) — ask them, or send them "
                   "the operations to save from the connector's page.")


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="http_setup_link", annotations=LECTURE)
    def http_setup_link(label: str, base_url: Optional[str] = None,
                        auth_mode: Optional[str] = None,
                        header_name: Optional[str] = None,
                        query_param: Optional[str] = None,
                        doc_path: Optional[str] = None) -> dict:
        """Link to connect a tool that has no dedicated connector (a payroll, ERP or
        any SaaS with an API key) through the Custom API connector. Returns `{url,
        instructions}`: send the url to the person who holds the key; they paste the
        key in the form, never in the chat — this tool takes no secret. If a key was
        already pasted in a chat, tell them to revoke it and create a new one.

        Args:
            label: name of the tool to connect (e.g. "Acme Payroll").
            base_url: API root to pre-fill (e.g. https://api.acme.test/v1), no query.
            auth_mode: bearer | header | query | basic | oauth2 | none.
            header_name: header NAME carrying the key, header mode (e.g. x-api-key).
            query_param: parameter NAME carrying the key, query mode.
            doc_path: path of the API's OpenAPI JSON, relative to base_url.
        """
        sub = _sub()
        params = setup_params(label, base_url, auth_mode, header_name, query_param,
                              doc_path)
        org = access.current_org(sub)
        url = links.connector_setup_link("http", sub=sub, org=org, **params)
        out = {"url": url, "prefilled": params, "instructions": INSTRUCTIONS}
        if url is None:
            out["url_missing"] = links.raison_sans_lien("connectors", sub=sub, org=org)
        return out

    @mcp.tool(name="http_operations", annotations=LECTURE)
    def http_operations(op: Literal["list", "import"] = "list",
                        source: Optional[Literal["doc_path", "url", "inline"]] = None,
                        url: Optional[str] = None,
                        document: Optional[dict] = None) -> dict:
        """Operations described for the Custom API account of this call (`_account`
        when there are several). `op="list"` returns them (name, description, method,
        path, input schema) — call one with `http_call`. `op="import"` (admin) turns
        an OpenAPI/Swagger JSON document into candidates WITHOUT saving them; review,
        then save with `http_set_operations`.

        Args:
            op: list (default) | import.
            source: import only — doc_path (the card's doc route, same auth), url
                (a public JSON document, no auth sent) or inline (`document`).
            url: source=url only.
            document: source=inline only — the OpenAPI JSON document.
        """
        sub = _sub()
        rc = _resolved()
        if op == "list":
            ops = _load(rc)
            return {"account": rc.account, "operations": [custom_ops.summary(o)
                                                          for o in ops]}
        _require_admin(sub, rc)
        try:
            cands, skipped = store.import_candidates(
                source or "", url=url, document=document,
                fields=rc.fields if source == "doc_path" else None)
        except store.ImportFailed as e:
            raise _bad(f"import: {e}")
        return {"account": rc.account, "operations": cands, "skipped": skipped,
                "saved": False}

    @mcp.tool(name="http_set_operations")
    def http_set_operations(operations: list[dict]) -> dict:
        """Save (REPLACE) the operations of the Custom API account of this call
        (admin). Each: `name` (snake_case), `description` (what the agent reads),
        `method` GET|POST|PUT|PATCH|DELETE, `path` relative with `{param}`, `input`
        (JSON schema object: properties, required), optional `in` {arg: path|query|body}
        — default: path if in the template, else query for GET/DELETE, body otherwise.
        At most 100. An invalid list is refused whole, every problem named.

        Args:
            operations: the full list; [] clears it.
        """
        sub = _sub()
        rc = _resolved()
        _require_admin(sub, rc)
        try:
            saved = store.save(rc.entity_type, str(rc.entity_id), rc.account, operations)
        except custom_ops.OperationsInvalid as e:
            raise _bad("operations refused: " + " | ".join(e.errors[:20]))
        except store.InstanceMissing as e:
            raise _bad(f"http connector: {e}.")
        return {"account": rc.account, "saved": len(saved),
                "names": [o["name"] for o in saved]}

    @mcp.tool(name="http_call")
    def http_call(operation: str, args: Optional[dict] = None,
                  dry_run: bool = False) -> dict:
        """Call a described operation of the Custom API account of this call, by
        name (list them with `http_operations`). Arguments are checked against its
        input schema, then sent where it declares (path, query or body), with the
        account's auth. `dry_run=true` returns the request without sending it.

        Args:
            operation: name of the operation.
            args: its arguments, by name.
            dry_run: validate and return {method, path, query, body} without calling.
        """
        _sub()
        rc = _resolved()
        ops = _load(rc)
        found = custom_ops.find(ops, operation)
        if found is None:
            names = ", ".join(o["name"] for o in ops[:30]) or "none described yet"
            raise _bad(f"no operation `{operation}` on this Custom API account — "
                       f"available: {names}.")
        try:
            plan = custom_ops.plan_call(found, args)
        except custom_ops.CallRefused as e:
            raise _bad(str(e))
        if dry_run:
            return {"dry_run": True, **plan}
        client = http_tool.client_from_fields(rc.fields)
        try:
            return client.request(plan["method"], plan["path"], params=plan["query"] or None,
                                  json=plan["body"])
        except ValueError as e:
            raise http_tool._value_error(e)
        except requests.HTTPError as e:
            raise http_tool._upstream_error(e)
