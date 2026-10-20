"""`http` connector, second module: `http`, the READER of a Custom API (the pair's
convention, `tests/test_convention_act.py`: `http` reads, `http_act` acts).

`tools/http.py` reaches a configured API by raw path (`http_get`/`http_post`/`http_doc`,
the connector's tools from before the pair). This module adds what makes the connector
usable for a tool we do not ship, without changing anything:

- `http(op="setup_link")` — the link a person opens to connect the API THEMSELVES, the
  key typed in the form. A raw secret is never an MCP argument (CLAUDE.md): the tool
  has no parameter that could carry one. Building a link writes nothing.
- `http(op="list")` — the org's custom connectors, or one connector's saved definition
  (`custom_ops_definition`: label, description, constant headers, variables, actions),
  stored on the credential's instance (`connectors/http_operations`).
- everything that writes or reaches the API — calls, OpenAPI import, saving the
  definition — is `http_act`, in `http_calls.py`.

The row whose definition is read or written is the one the cascade RESOLVES for the
call (`_account`, team, org) — the same key the calls use.
"""
from __future__ import annotations

import re
from typing import Literal, Optional
from urllib.parse import urlsplit

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, custom_ops, egress, links, roles, session_org
from ..auth.hooks import current_user_sub_from_token
from ..connectors import http_operations as store
from ..mcp_errors import McpError
from ..providers.http import AUTH_MODES
from .lecture import LECTURE

# A header name is an RFC 9110 token — `api_key` (underscore) is one, and real APIs use it.
_HEADER_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
_PARAM_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,64}$")

INSTRUCTIONS = (
    "Send this link to the person who holds the API key. They open it, check the "
    "pre-filled fields and paste the key IN THE FORM: oto keeps it encrypted in the "
    "vault. Never ask for the key in the conversation and never pass it to a tool. "
    "If a key was already pasted in a chat, ask them to revoke it at the provider and "
    "create a new one before connecting. Once saved, the API is reachable with "
    "`http_get`/`http_post`, and by name with `http_act(op=\"call\")` once operations "
    "are described.")


def _bad(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def refuse_foreign(tool: str, op: str, given: dict, allowed: tuple) -> None:
    """A multiplexed tool refuses an argument its `op` does not read — named, so the
    agent never believes it was applied."""
    foreign = sorted(k for k, v in given.items() if v is not None and k not in allowed)
    if foreign:
        raise _bad(f"`{tool}(op=\"{op}\")` does not take "
                   + ", ".join(f"`{k}`" for k in foreign)
                   + (f" — it takes: {', '.join(f'`{a}`' for a in allowed)}." if allowed
                      else "."))


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


def _resolved(account: Optional[str] = None):
    """The vault row the cascade serves for this call — `account` when given (an
    action resolved by name), else `_account`, team, org."""
    rc = access.resolve_credential("http", want="byo", account=account)
    if rc.entity_type is None:
        raise _bad("http connector: no Custom API account of yours, your team's or your "
                   "org's resolves — connect one first (`http(op=\"setup_link\")`).")
    return rc


def _definition(rc) -> dict:
    try:
        return store.load_definition(rc.entity_type, str(rc.entity_id), rc.account)
    except store.InstanceMissing as e:
        raise _bad(f"http connector: {e}.")


def _org_connectors(sub: str) -> list[dict]:
    """The custom connectors of the caller's org — empty without an org."""
    org = access.current_org(sub)
    return store.org_connectors(org) if org else []


def _auth_header(rc) -> Optional[str]:
    f = rc.fields
    return f.get("header_name") if (f.get("auth_mode") or "") == "header" else None


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
    @mcp.tool(name="http", annotations=LECTURE)
    def http(op: Literal["list", "setup_link"] = "list",
             label: Optional[str] = None, base_url: Optional[str] = None,
             auth_mode: Optional[str] = None, header_name: Optional[str] = None,
             query_param: Optional[str] = None,
             doc_path: Optional[str] = None) -> dict:
        """Read the org's custom connectors (Custom API) — ops: `list`, `setup_link`.
        Calls, import and changes: `http_act`.

        - `list` (default): without `_account`, every custom connector of the org
          (tools connected once for every member: account, label, description, action
          names) — start here. With `_account`: that connector's definition, its
          actions with their input schema. Call an action with
          `http_act(op="call", operation=…, args={…})`.
        - `setup_link`: a link to connect a tool that has no dedicated connector (a
          payroll, ERP or any SaaS with an API key) → `{url, prefilled,
          instructions}`. Send the url to the person who holds the key; they paste the
          key in the form, never in the chat — this tool takes no secret. If a key was
          already pasted in a chat, tell them to revoke it and create a new one.

        Args:
            op: list (default) | setup_link.
            label: setup_link — name of the tool to connect (e.g. "Acme Payroll").
            base_url: setup_link — API root to pre-fill (e.g. https://api.acme.test/v1),
                no query.
            auth_mode: setup_link — bearer | header | query | basic | oauth2 | none.
            header_name: setup_link — header NAME carrying the key, header mode
                (e.g. x-api-key).
            query_param: setup_link — parameter NAME carrying the key, query mode.
            doc_path: setup_link — path of the API's OpenAPI JSON, relative to base_url.
        """
        setup = {"label": label, "base_url": base_url, "auth_mode": auth_mode,
                 "header_name": header_name, "query_param": query_param,
                 "doc_path": doc_path}
        sub = _sub()
        if op == "setup_link":
            params = setup_params(label, base_url, auth_mode, header_name, query_param,
                                  doc_path)
            org = access.current_org(sub)
            url = links.connector_setup_link("http", sub=sub, org=org, **params)
            out = {"url": url, "prefilled": params, "instructions": INSTRUCTIONS}
            if url is None:
                out["url_missing"] = links.raison_sans_lien("connectors", sub=sub, org=org)
            return out
        refuse_foreign("http", op, setup, ())
        if session_org.current_call_account() is None:
            conns = _org_connectors(sub)
            if conns:
                return {"connectors": [
                    {**{k: v for k, v in c.items() if k != "operations"},
                     "actions": [o["name"] for o in c["operations"]]} for c in conns],
                    "next": "http(op=\"list\", _account=<account>) for an action's "
                            "input; http_act(op=\"call\", operation=<action>, "
                            "args={…}) to call it."}
        rc = _resolved()
        d = _definition(rc)
        return {"account": rc.account, "label": d["label"],
                "description": d["description"], "variables": d["variables"],
                "operations": [custom_ops.summary(o) for o in d["operations"]]}
