"""Where the `http` connector's described operations live, and where an import reads from.

The operations of one `http` credential live in `connector_instances.config
["operations"]` of THE instance that names its vault row — designated by the vault's
own quadruplet (owner type, owner id, `http`, account), never by anything else. The
two faces go through here: the agent's tools (`tools/http_ops.py`, the row the
cascade resolved for the call) and the org's REST routes
(`capabilities/connectors/http_operations.py`, the org's row for a named account).

Nothing here resolves a credential: the caller hands over the row (and, to read the
API's `doc_path`, its fields). The format and its validation are `custom_ops`; the
OpenAPI conversion is `custom_ops_openapi`.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests

from .. import credentials_store, custom_ops, custom_ops_definition, custom_ops_openapi, egress
from ..db import connector_instances

logger = logging.getLogger(__name__)

CONNECTOR = "http"
CONFIG_KEY = "operations"
# An API description worth importing fits well under this; beyond, it is not read.
MAX_DOC_BYTES = 2 * 1024 * 1024
# (connect, read) for a PUBLIC document URL — bounded at this level (#892).
URL_TIMEOUT = (5, 20)


class InstanceMissing(LookupError):
    """No live instance names this vault row — no credential, or it was removed."""


class ImportFailed(ValueError):
    """The source could not be read as an OpenAPI/Swagger JSON document."""


def load(owner_type: str, owner_id: str, account: str = "") -> list[dict]:
    """The described operations of this vault row's instance (`[]` if none yet)."""
    inst = connector_instances.live_instance_config(owner_type, owner_id, CONNECTOR,
                                                    account)
    if inst is None:
        raise InstanceMissing(f"no live `http` instance for account `{account}`")
    ops = (inst["config"] or {}).get(CONFIG_KEY)
    return ops if isinstance(ops, list) else []


def _instance(owner_type: str, owner_id: str, account: str) -> dict:
    inst = connector_instances.live_instance_config(owner_type, owner_id, CONNECTOR,
                                                    account)
    if inst is None:
        raise InstanceMissing(f"no live `http` instance for account `{account}`")
    return inst


def _write(owner_type: str, owner_id: str, account: str, patch: dict) -> None:
    inst = _instance(owner_type, owner_id, account)
    if not connector_instances.merge_instance_config(inst["id"], patch):
        raise InstanceMissing(f"no live `http` instance for account `{account}`")


def save(owner_type: str, owner_id: str, account: str, ops: Any) -> list[dict]:
    """Validate (`custom_ops.OperationsInvalid` on refusal), then REPLACE the list."""
    clean = custom_ops.validate_operations(ops)
    _write(owner_type, owner_id, account, {CONFIG_KEY: clean})
    return clean


def definition_of(config: dict) -> dict:
    """The saved definition read from an instance's config, every key present."""
    config = config or {}
    ops = config.get(CONFIG_KEY)
    return {"label": config.get("label"), "description": config.get("description"),
            "logo_domain": config.get("logo_domain"),
            "headers": config.get("headers") or {},
            "variables": config.get("variables") or {},
            "operations": ops if isinstance(ops, list) else []}


def load_definition(owner_type: str, owner_id: str, account: str = "") -> dict:
    return definition_of(_instance(owner_type, owner_id, account)["config"])


def save_definition(owner_type: str, owner_id: str, account: str, definition: Any, *,
                    auth_header: Optional[str] = None) -> dict:
    """Validate the keys given (`custom_ops.OperationsInvalid`), write them — the
    keys NOT given stay as they are — and return the whole definition."""
    patch = custom_ops_definition.validate_definition(definition, auth_header=auth_header)
    if patch:
        _write(owner_type, owner_id, account, patch)
    return load_definition(owner_type, owner_id, account)


def _summary(account: str, config: dict, meta_label: Optional[str] = None) -> dict:
    d = definition_of(config)
    return {"account": account,
            "label": d["label"] or meta_label or account or "Custom API",
            "description": d["description"], "logo_domain": d["logo_domain"],
            "actions_count": len(d["operations"])}


def org_connectors(org_id: int) -> list[dict]:
    """The org's custom connectors, one per `http` account: `{account, label,
    description, logo_domain, actions_count}` — and `operations` (the full list), for
    the caller that resolves an action by name. Two queries, no decryption: the
    label is the definition's, else the card's (`meta.label`), else the account."""
    metas = {r["account"]: (r.get("meta") or {}).get("label") for r in
             credentials_store.list_accounts(credentials_store.ORG, str(org_id), CONNECTOR)}
    out = []
    for inst in connector_instances.live_instance_configs(
            credentials_store.ORG, str(org_id), CONNECTOR):
        if inst["account"] not in metas:
            continue
        row = _summary(inst["account"], inst["config"], metas[inst["account"]])
        row["operations"] = definition_of(inst["config"])["operations"]
        out.append(row)
    return out


def match_org_connector(org_id: int, name: str) -> Optional[dict]:
    """The org's custom connector whose label (or account) IS `name`, case and
    surrounding spaces ignored — what `oto_connector(name=…)` returns for a tool
    configured through Custom API instead of "unknown connector"."""
    q = " ".join((name or "").lower().split())
    if not q or not org_id:
        return None
    for c in org_connectors(org_id):
        if q in {" ".join(str(c["label"]).lower().split()), c["account"].lower()}:
            return {"connector": CONNECTOR, "account": c["account"], "label": c["label"],
                    "description": c["description"], "logo_domain": c["logo_domain"],
                    "actions_count": c["actions_count"],
                    "how_to_call": (f"http_operations(_account=\"{c['account']}\") lists "
                                    "its actions; http_call(operation=…, args={…}) "
                                    "calls one.")}
    return None


def summaries_for_instances(ids: list[int]) -> dict[int, dict]:
    """`{instance id: {label, description, logo_domain, actions_count}}` — what the
    front's instance roster shows per custom connector. One query."""
    return {iid: {k: v for k, v in _summary("", cfg).items() if k != "account"}
            for iid, cfg in connector_instances.configs_for_instance_ids(ids).items()}


def with_constant_headers(client, headers: Optional[dict]):
    """Apply the definition's constant headers to an oto-core client.

    oto-core's `HttpConnectorClient` takes no `headers=` (follow-up: add that
    parameter in oto-core, then drop this). Until then the headers go on the
    client's session when it is first prepared — AFTER the auth configured it, and
    a definition can never name an authenticating header (`custom_ops_definition`),
    so the auth always wins. Coupled to one private hook, `_ready`, wrapped rather
    than replaced."""
    if not headers:
        return client
    ready = client._ready

    def _ready_with_headers():
        session = ready()
        for name, value in headers.items():
            session.headers.setdefault(name, value)
        return session
    client._ready = _ready_with_headers
    return client


def client_for(fields: dict):
    """The oto-core client for these credential fields, behind the egress guard on
    BOTH destinations a card carries (`base_url`, and `token_url` in oauth2 mode).
    Raises `egress.EgressRefused` or `ValueError` (incomplete or invalid config)."""
    from oto.tools.http import HttpConnectorClient
    base_url = (fields.get("base_url") or "").strip()
    mode = (fields.get("auth_mode") or "").strip()
    if not base_url or not mode:
        raise ValueError("http connector not configured: `base_url` and `auth_mode` "
                         "are required on the Custom API card")
    egress.check_url(base_url, connector=CONNECTOR, field="base_url")
    token_url = (fields.get("token_url") or "").strip()
    if mode.lower() == "oauth2" and token_url:
        egress.check_url(token_url, connector=CONNECTOR, field="token_url")
    return HttpConnectorClient(base_url, mode, fields, timeout=45)


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        raise ImportFailed("the document is not JSON (YAML is not read: export the "
                           "OpenAPI description as JSON)") from None


def fetch_public_document(url: str) -> Any:
    """GET a PUBLIC document URL: no auth sent, egress guard without the named
    exceptions (an address typed by a person or an agent — `egress.check_url`),
    no redirect followed, size and time bounded."""
    try:
        egress.check_url(url, connector=CONNECTOR, field="url",
                         exceptions_declarees=False)
    except egress.EgressRefused as e:
        raise ImportFailed(str(e)) from None
    try:
        with requests.get(url, stream=True, timeout=URL_TIMEOUT, allow_redirects=False,
                          headers={"Accept": "application/json"}) as r:
            if 300 <= r.status_code < 400:
                raise ImportFailed(f"the URL redirects ({r.status_code}) — give the "
                                   "final address of the document")
            if r.status_code >= 400:
                raise ImportFailed(f"the URL answered HTTP {r.status_code}")
            data = bytearray()
            for chunk in r.iter_content(64 * 1024):
                data += chunk
                if len(data) > MAX_DOC_BYTES:
                    raise ImportFailed(f"the document exceeds {MAX_DOC_BYTES // 1024 // 1024} MB")
    except requests.RequestException as e:
        raise ImportFailed(f"the URL could not be read ({type(e).__name__})") from None
    return _parse(bytes(data))


def fetch_doc_path_document(fields: dict) -> Any:
    """The API's own description at the card's `doc_path`, read WITH the
    credential's auth through the same client as every call."""
    doc_path = (fields.get("doc_path") or "").strip()
    if not doc_path.startswith("/"):
        raise ImportFailed("no `doc_path` on this account's Custom API card (e.g. "
                           "/openapi.json) — use a public URL or paste the document")
    try:
        raw, _ = client_for(fields).get_raw(doc_path, max_bytes=MAX_DOC_BYTES)
    except egress.EgressRefused as e:
        raise ImportFailed(str(e)) from None
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        raise ImportFailed(f"`{doc_path}` answered HTTP {status}") from None
    except requests.RequestException as e:
        raise ImportFailed(f"`{doc_path}` could not be read ({type(e).__name__})") from None
    except ValueError as e:      # size cap, redirect refused, invalid card
        raise ImportFailed(f"`{doc_path}`: {e}") from None
    return _parse(raw)


def import_candidates(source: str, *, url: Optional[str] = None,
                      document: Any = None,
                      fields: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
    """Candidate operations from `doc_path` | `url` | `inline`. Saves nothing."""
    if source == "doc_path":
        doc = fetch_doc_path_document(fields or {})
    elif source == "url":
        if not url:
            raise ImportFailed("`url` is required with source=url")
        doc = fetch_public_document(url)
    elif source == "inline":
        if isinstance(document, str):
            doc = _parse(document.encode())
        else:
            doc = document
        if len(json.dumps(doc or {})) > MAX_DOC_BYTES:
            raise ImportFailed("the document exceeds 2 MB")
    else:
        raise ImportFailed("`source` must be doc_path, url or inline")
    try:
        return custom_ops_openapi.from_openapi(doc)
    except custom_ops_openapi.NotAnOpenApiDocument as e:
        raise ImportFailed(str(e)) from None
