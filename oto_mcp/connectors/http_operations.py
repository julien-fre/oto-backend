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

from .. import custom_ops, custom_ops_openapi, egress
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


def save(owner_type: str, owner_id: str, account: str, ops: Any) -> list[dict]:
    """Validate (`custom_ops.OperationsInvalid` on refusal), then REPLACE the list."""
    clean = custom_ops.validate_operations(ops)
    inst = connector_instances.live_instance_config(owner_type, owner_id, CONNECTOR,
                                                    account)
    if inst is None or not connector_instances.set_instance_config_key(
            inst["id"], CONFIG_KEY, clean):
        raise InstanceMissing(f"no live `http` instance for account `{account}`")
    return clean


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
