"""`http` connector, second module: setting up a Custom API, and its described operations.

`tools/http.py` reaches a configured API by raw path (`http_get`/`http_post`). This
module adds what makes the connector usable for a tool we do not ship:

- `http_setup_link` — the link a person opens to connect the API THEMSELVES, the key
  typed in the form. A raw secret is never an MCP argument (CLAUDE.md): the tool has
  no parameter that could carry one.
- `http_operations` / `http_set_definition` — the org's custom connectors and the
  definition each one SAVES once for every member (`custom_ops_definition`: label,
  description, constant headers, variables, actions), stored on the credential's
  instance (`connectors/http_operations`).
- the calls (`http_call` by action name, `http_request` raw) live in `http_calls.py`.

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


def _resolved(account: Optional[str] = None):
    """The vault row the cascade serves for this call — `account` when given (an
    action resolved by name), else `_account`, team, org."""
    rc = access.resolve_credential("http", want="byo", account=account)
    if rc.entity_type is None:
        raise _bad("http connector: no Custom API account of yours, your team's or your "
                   "org's resolves — connect one first (`http_setup_link`).")
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
        """The org's custom connectors (tools connected through Custom API, configured
        once for every member) and their actions. Without `_account`: every custom
        connector (account, label, description, action names) — start here. With
        `_account`: that connector's definition, actions with their input schema.
        Call an action with `http_call`. `op="import"` (admin) turns an
        OpenAPI/Swagger JSON document into candidate actions WITHOUT saving them;
        review, then save with `http_set_definition`.

        Args:
            op: list (default) | import.
            source: import only — doc_path (the card's doc route, same auth), url
                (a public JSON document, no auth sent) or inline (`document`).
            url: source=url only.
            document: source=inline only — the OpenAPI JSON document.
        """
        sub = _sub()
        if op == "list" and session_org.current_call_account() is None:
            conns = _org_connectors(sub)
            if conns:
                return {"connectors": [
                    {**{k: v for k, v in c.items() if k != "operations"},
                     "actions": [o["name"] for o in c["operations"]]} for c in conns],
                    "next": "http_operations(_account=<account>) for an action's input; "
                            "http_call(operation=<action>, args={…}) to call it."}
        rc = _resolved()
        if op == "list":
            d = _definition(rc)
            return {"account": rc.account, "label": d["label"],
                    "description": d["description"], "variables": d["variables"],
                    "operations": [custom_ops.summary(o) for o in d["operations"]]}
        _require_admin(sub, rc)
        try:
            cands, skipped = store.import_candidates(
                source or "", url=url, document=document,
                fields=rc.fields if source == "doc_path" else None)
        except store.ImportFailed as e:
            raise _bad(f"import: {e}")
        return {"account": rc.account, "operations": cands, "skipped": skipped,
                "saved": False}

    @mcp.tool(name="http_set_definition")
    def http_set_definition(operations: Optional[list[dict]] = None,
                            label: Optional[str] = None,
                            description: Optional[str] = None,
                            logo_domain: Optional[str] = None,
                            headers: Optional[dict] = None,
                            variables: Optional[dict] = None) -> dict:
        """Save the definition of the custom connector of this call (`_account`;
        admin), reused by every member and session. Only the parts given change.
        `operations` REPLACES the action list: each `name` (snake_case),
        `description`, `method` GET|POST|PUT|PATCH|DELETE, `path` relative with
        `{param}`, `input` (JSON schema object), optional `in` {arg: path|query|body}.
        Never a secret here: the key stays on the card.

        Args:
            operations: the full action list (100 max); [] clears it.
            label: display name of the connector (e.g. "Acme Payroll").
            description: what the tool is and what it holds, for the agent.
            logo_domain: brand domain for the logo (e.g. acme.test).
            headers: constant NON-secret headers (e.g. an API version).
            variables: saved values {name: {value, description}} that fill an action
                argument of the same name when the call omits it.
        """
        sub = _sub()
        rc = _resolved()
        _require_admin(sub, rc)
        given = {k: v for k, v in (("operations", operations), ("label", label),
                                   ("description", description),
                                   ("logo_domain", logo_domain), ("headers", headers),
                                   ("variables", variables)) if v is not None}
        try:
            d = store.save_definition(rc.entity_type, str(rc.entity_id), rc.account,
                                      given, auth_header=_auth_header(rc))
        except custom_ops.OperationsInvalid as e:
            raise _bad("definition refused: " + " | ".join(e.errors[:20]))
        except store.InstanceMissing as e:
            raise _bad(f"http connector: {e}.")
        return {"account": rc.account, "saved": sorted(given),
                "label": d["label"], "actions": [o["name"] for o in d["operations"]]}
