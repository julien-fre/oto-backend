"""Capabilities "Custom API operations" — the org's REST face of the `http` connector's
described operations (`custom_ops`), for the dashboard's Custom API page.

Same permission split as the org's credential itself (`orgs/secrets.py`): any member
READS the operations (they are what the agent calls), only an org admin WRITES or
IMPORTS them — the same `ORG_ADMIN_OF` as setting the key. The row is the org's
vault row for the named `account` (`""` = the single account); without one, 404:
operations describe a connected API, never an absent one.

The agent's face is `tools/http_ops.py` (row resolved by the cascade for the call);
both go through `connectors/http_operations.py`, where the list is stored.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ... import credentials_store, custom_ops, providers
from ...connectors import http_operations as store
from .._authz import ORG_ADMIN_OF, ORG_MEMBER_OF
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}
_PATH = "/api/orgs/{id}/connectors/http/operations"
_DEF_PATH = "/api/orgs/{id}/connectors/http/definition"
_DOC_ACCOUNT = ("Custom API account of the org (`\"\"` = the single account). "
                "404 `http_credential_not_found` when the org has no such account.")


class HttpOperations(BaseModel):
    """The described operations of one Custom API account of the org. Each:
    `name`, `description`, `method`, `path` (relative, `{param}` placeholders),
    `input` (JSON schema object), optional `in` {argument: path|query|body}."""
    account: str
    operations: list[dict]


class HttpOperationsImport(BaseModel):
    """CANDIDATES converted from an OpenAPI/Swagger JSON document — nothing is saved:
    the page reviews them, then `PUT`s the list it keeps. `skipped` = what the
    format cannot express, with the reason (`{path, method, reason}`)."""
    account: str
    operations: list[dict]
    skipped: list[dict]


class GetInput(BaseModel):
    org_id: int
    account: str = Field(default="", description=_DOC_ACCOUNT)


class SetInput(BaseModel):
    org_id: int
    account: str = Field(default="", description=_DOC_ACCOUNT)
    operations: list[Any] = Field(description=(
        "The FULL list (replaces, never merges; `[]` clears). At most 100, unique "
        "snake_case names. Invalid ⇒ 400 `invalid_operations`, `details.errors` lists "
        "every problem."))


class ImportInput(BaseModel):
    org_id: int
    account: str = Field(default="", description=_DOC_ACCOUNT)
    source: Literal["doc_path", "url", "inline"] = Field(description=(
        "`doc_path` = the API's own document at the card's doc route, read with the "
        "account's auth; `url` = a PUBLIC JSON document (no auth sent, 2 MB max); "
        "`inline` = the document in `document`."))
    url: Optional[str] = None
    document: Optional[Any] = None


def _row(org_id: int, account: str) -> str:
    """The account name, once the org's vault row is known to exist."""
    account = (account or "").strip()
    accounts = {r["account"] for r in credentials_store.list_accounts(
        credentials_store.ORG, str(org_id), store.CONNECTOR)}
    if account not in accounts:
        raise AuthzDenied(404, "http_credential_not_found", (
            f"Org #{org_id} has no Custom API account `{account}`"
            + (f" (accounts: {', '.join(repr(a) for a in sorted(accounts))})."
               if accounts else " — connect the API first.")))
    return account


def _load(org_id: int, account: str) -> list[dict]:
    try:
        return store.load(credentials_store.ORG, str(org_id), account)
    except store.InstanceMissing:
        # The vault row exists (checked by `_row`) but no live instance names it:
        # nothing was ever described on it, and nothing can be until it is named.
        return []


def _get(ctx: ResolvedCtx, inp: GetInput) -> dict:
    account = _row(inp.org_id, inp.account)
    return {"account": account, "operations": _load(inp.org_id, account)}


def _set(ctx: ResolvedCtx, inp: SetInput) -> dict:
    account = _row(inp.org_id, inp.account)
    try:
        saved = store.save(credentials_store.ORG, str(inp.org_id), account,
                           inp.operations)
    except custom_ops.OperationsInvalid as e:
        raise AuthzDenied(400, "invalid_operations",
                          f"{len(e.errors)} problem(s) in the operations: {e}",
                          # A dict, like every `details` (`Erreur` in /openapi.json).
                          details={"errors": e.errors})
    except store.InstanceMissing:
        raise AuthzDenied(409, "http_instance_missing", (
            "The account's credential has no live instance to carry its operations — "
            "set the key again on the Custom API card, then retry."))
    return {"account": account, "operations": saved}


def _import(ctx: ResolvedCtx, inp: ImportInput) -> dict:
    account = _row(inp.org_id, inp.account)
    fields = None
    if inp.source == "doc_path":
        row = credentials_store.get_credential_with_meta(
            credentials_store.ORG, str(inp.org_id), store.CONNECTOR, account)
        fields = credentials_store.unpack_secret(store.CONNECTOR, row["secret"]) if row else {}
    try:
        cands, skipped = store.import_candidates(inp.source, url=inp.url,
                                                 document=inp.document, fields=fields)
    except store.ImportFailed as e:
        raise AuthzDenied(400, "import_failed", str(e))
    return {"account": account, "operations": cands, "skipped": skipped}


# The card's NON-secret fields a definition export carries — those that let another
# org rebuild the same connector. Filtered AGAIN by the registry's `secret` flag at
# export: a field turned secret tomorrow leaves the export by itself.
_CARD_FIELDS = ("label", "base_url", "auth_mode", "header_name", "query_param",
                "doc_path", "token_url", "scope")


class HttpDefinition(BaseModel):
    """A custom connector's definition, NEVER a secret: the card's non-secret fields
    (`label`, `base_url`, `auth_mode`, `header_name`, `query_param`, `doc_path`,
    `token_url`, `scope` — present when set) and the saved definition
    (`description`, `logo_domain`, `headers`, `variables` with their values,
    `operations`). Export it from one org, `PUT` its definition part in another."""
    model_config = ConfigDict(extra="allow")
    account: str
    label: Optional[str] = None
    description: Optional[str] = None
    logo_domain: Optional[str] = None
    headers: dict
    variables: dict
    operations: list[dict]


class DefinitionSetInput(BaseModel):
    org_id: int
    account: str = Field(default="", description=_DOC_ACCOUNT)
    label: Optional[str] = None
    description: Optional[str] = None
    logo_domain: Optional[str] = None
    headers: Optional[dict] = None
    variables: Optional[dict] = None
    operations: Optional[list[Any]] = Field(default=None, description=(
        "When given, REPLACES the action list. Parts not given stay as they are."))


def _card(org_id: int, account: str) -> dict:
    """The card's non-secret fields — decrypted in-process, secret ones dropped."""
    row = credentials_store.get_credential_with_meta(
        credentials_store.ORG, str(org_id), store.CONNECTOR, account)
    if not row:
        return {}
    fields = {**credentials_store.unpack_secret(store.CONNECTOR, row["secret"]),
              **credentials_store.public_meta(row.get("meta"))}
    secrets = {f.name for f in providers.REGISTRY[store.CONNECTOR].secret_fields if f.secret}
    return {k: fields[k] for k in _CARD_FIELDS
            if k not in secrets and fields.get(k) not in (None, "")}


def _definition_out(org_id: int, account: str) -> dict:
    try:
        d = store.load_definition(credentials_store.ORG, str(org_id), account)
    except store.InstanceMissing:
        d = store.definition_of({})
    card = _card(org_id, account)
    return {"account": account, **card, **{k: v for k, v in d.items()
                                           if v is not None or k not in card}}


def _get_definition(ctx: ResolvedCtx, inp: GetInput) -> dict:
    return _definition_out(inp.org_id, _row(inp.org_id, inp.account))


def _set_definition(ctx: ResolvedCtx, inp: DefinitionSetInput) -> dict:
    account = _row(inp.org_id, inp.account)
    given = {k: getattr(inp, k) for k in ("label", "description", "logo_domain",
                                          "headers", "variables", "operations")
             if getattr(inp, k) is not None}
    card = _card(inp.org_id, account)
    auth_header = card.get("header_name") if card.get("auth_mode") == "header" else None
    try:
        store.save_definition(credentials_store.ORG, str(inp.org_id), account, given,
                              auth_header=auth_header)
    except custom_ops.OperationsInvalid as e:
        raise AuthzDenied(400, "invalid_definition",
                          f"{len(e.errors)} problem(s) in the definition: {e}",
                          details={"errors": e.errors})
    except store.InstanceMissing:
        raise AuthzDenied(409, "http_instance_missing", (
            "The account's credential has no live instance to carry its definition — "
            "set the key again on the Custom API card, then retry."))
    return _definition_out(inp.org_id, account)


_NOT_FOUND = DeclaredError(404, "http_credential_not_found",
                           "the org has no Custom API (`http`) credential for this account")

CAPABILITIES += [
    Capability(
        key="custom_api.operations.get", handler=_get, Input=GetInput,
        authz=ORG_MEMBER_OF("org_id"), Output=HttpOperations,
        description="Read the described operations of an org's Custom API account.",
        errors=(_NOT_FOUND,),
        rest=RestBinding("GET", _PATH, _ID),
    ),
    Capability(
        key="custom_api.operations.set", handler=_set, Input=SetInput,
        authz=ORG_ADMIN_OF("org_id"), Output=HttpOperations,
        description=("Replace the described operations of an org's Custom API account "
                     "(validated whole; org admin)."),
        errors=(_NOT_FOUND,
                DeclaredError(400, "invalid_operations",
                              "the list breaks the format — `details.errors` names each problem"),
                DeclaredError(409, "http_instance_missing",
                              "the credential has no live instance to carry the list")),
        rest=RestBinding("PUT", _PATH, _ID),
    ),
    Capability(
        key="custom_api.operations.import", handler=_import, Input=ImportInput,
        authz=ORG_ADMIN_OF("org_id"), Output=HttpOperationsImport,
        description=("Convert an OpenAPI/Swagger JSON document into candidate operations "
                     "for an org's Custom API account, without saving (org admin)."),
        errors=(_NOT_FOUND,
                DeclaredError(400, "import_failed",
                              "the source cannot be read as an OpenAPI/Swagger JSON "
                              "document (unreachable, refused address, too large, not JSON)")),
        rest=RestBinding("POST", _PATH + "/import", _ID),
    ),
    Capability(
        key="custom_api.definition.get", handler=_get_definition, Input=GetInput,
        authz=ORG_MEMBER_OF("org_id"), Output=HttpDefinition,
        description=("Export the definition of an org's Custom API connector — the "
                     "card's non-secret fields and the saved definition, never a secret."),
        errors=(_NOT_FOUND,),
        rest=RestBinding("GET", _DEF_PATH, _ID),
    ),
    Capability(
        key="custom_api.definition.set", handler=_set_definition,
        Input=DefinitionSetInput, authz=ORG_ADMIN_OF("org_id"), Output=HttpDefinition,
        description=("Save the definition of an org's Custom API connector (label, "
                     "description, logo domain, constant headers, variables, actions; "
                     "only the parts given; org admin)."),
        errors=(_NOT_FOUND,
                DeclaredError(400, "invalid_definition",
                              "the definition breaks the format — `details.errors` names "
                              "each problem"),
                DeclaredError(409, "http_instance_missing",
                              "the credential has no live instance to carry the definition")),
        rest=RestBinding("PUT", _DEF_PATH, _ID),
    ),
]
