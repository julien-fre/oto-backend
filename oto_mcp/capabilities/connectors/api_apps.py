"""Capabilities "Custom API apps" — the REST face of an org's Custom API APPLICATIONS
(`connectors/api_apps.py`, `docs/connector-vault.md` § Les applications de Custom API).

An app is one tool an org configures once — its address, its auth mode, its actions —
used through KEYS at any tier: a member's own, a team's, the org's. The keys are posed by
the usual routes (`me.credential.set`, `group.secret.set`, `org.secret.set`) under the
app's `account`, with their secret fields only: the app fills the rest and binds them.

- `GET    /api/orgs/{id}/connectors/api/apps`                — members: the org's apps,
  each with `access` (the tier of the key that serves the caller, `null` = none);
- `GET    /api/orgs/{id}/connectors/api/apps/{account}`      — members: one app, its
  connection (never a secret) and definition; for an org admin, also its `keys`
  (tier and owner of each, never a secret);
- `POST   /api/orgs/{id}/connectors/api/apps`                — org admin: create an app,
  or turn the org's existing Custom API card of that name into one (`adopt_org_key`);
- `PATCH  /api/orgs/{id}/connectors/api/apps/{account}`      — org admin: change its
  connection or definition (only the parts given) — `stale_keys` counts the keys that
  must be set again because the host or the auth mode changed;
- `POST   /api/orgs/{id}/connectors/api/apps/{account}/import` — org admin: candidate
  actions from an OpenAPI document (`url` or `inline`), saved by a PATCH;
- `DELETE /api/orgs/{id}/connectors/api/apps/{account}`      — org admin: retire it
  (archived); its keys stay in the vault and refuse by name until removed.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ... import credentials_store, roles
from ...connectors import api_apps as apps
from ...connectors import api_operations as ops_store
from ...db import api_apps as store
from .._authz import ORG_ADMIN_OF, ORG_MEMBER_OF
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}
_PATH = "/api/orgs/{id}/connectors/api/apps"
_ONE = _PATH + "/{account}"
_DEF_KEYS = ("label", "description", "logo_domain", "headers", "variables", "operations")


class ApiApp(BaseModel):
    """A Custom API app of the org, NEVER a secret: `account` (its id, the name its keys
    are set under), `label`, `description`, `logo_domain`, `actions_count`, `access`
    (`member` | `group` | `org` = the tier of the key that serves the caller, `null` =
    no key for them yet), `connection` (base_url, auth_mode and the other non-secret
    fields every key uses), `definition` (headers, variables, operations) — and, for an
    org admin, `keys`: one `{level, owner, set_at, set_by}` per key bound to it."""
    model_config = ConfigDict(extra="allow")
    account: str
    label: str
    description: Optional[str] = None
    logo_domain: Optional[str] = None
    actions_count: int
    access: Optional[str] = None


class ApiApps(BaseModel):
    """The org's Custom API apps, by account."""
    apps: list[ApiApp]


class ApiAppSaved(ApiApp):
    """The app after the change. `stale_keys` = keys set for another host or auth mode,
    which refuse until set again (`api_app_key_stale`)."""
    stale_keys: int = 0


class ApiAppImport(BaseModel):
    """CANDIDATE actions converted from an OpenAPI/Swagger JSON document — nothing is
    saved: review them, then PATCH the `operations` kept. `skipped` = what the format
    cannot express, with the reason."""
    account: str
    operations: list[dict]
    skipped: list[dict]


class ApiAppRemoved(BaseModel):
    """The app is retired. `keys_left` = keys still bound to it: they refuse by name
    (`api_app_removed`) until their holders remove them."""
    ok: bool
    keys_left: int


class ListInput(BaseModel):
    org_id: int


class OneInput(BaseModel):
    org_id: int
    account: str


class _DefinitionParts(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    logo_domain: Optional[str] = None
    headers: Optional[dict] = None
    variables: Optional[dict] = None
    operations: Optional[list[Any]] = Field(default=None, description=(
        "When given, REPLACES the action list."))


class CreateInput(_DefinitionParts):
    org_id: int
    account: str = Field(description=(
        "The app's name — the one its keys are set under (80 characters max, no `/`)."))
    connection: Optional[dict] = Field(default=None, description=(
        "base_url, auth_mode and, per mode, header_name / query_param / token_url / "
        "scope / session; doc_path optional. Never a secret. Omitted with "
        "`adopt_org_key`: taken from the org's card."))
    adopt_org_key: bool = Field(default=False, description=(
        "Turn the org's existing Custom API card of this account into the app: its "
        "connection and definition move to the app, its key becomes the app's org key."))


class PatchInput(_DefinitionParts):
    org_id: int
    account: str
    connection: Optional[dict] = Field(default=None, description=(
        "Only the fields given change; an empty value clears an optional one."))


class ImportInput(BaseModel):
    org_id: int
    account: str
    source: Literal["url", "inline"]
    url: Optional[str] = None
    document: Optional[Any] = None


def _refus(e: apps.AppRefused) -> AuthzDenied:
    # `details.errors`: each problem of a refused definition, as the card's routes say
    # them — the dashboard places them against the rows they name.
    return AuthzDenied(e.status, e.code, e.message,
                       **({"details": {"errors": e.errors}} if e.errors else {}))


def _app(org_id: int, account: str) -> dict:
    app = store.vivante(org_id, (account or "").strip())
    if app is None:
        raise AuthzDenied(404, "api_app_not_found",
                          f"No Custom API app `{account}` in org #{org_id}.")
    return app


def _vue(ctx: ResolvedCtx, app: dict) -> dict:
    org = int(app["org_id"])
    out = apps.apercu(app, avec_cles=roles.is_org_admin(ctx.sub, org))
    out["access"] = apps.acces(app, org, ctx.sub, apps.equipe_active(ctx.sub, org))
    for k in ("operations", "scope", "id"):
        out.pop(k, None)
    return out


def _definition_given(inp: _DefinitionParts) -> dict:
    return {k: getattr(inp, k) for k in _DEF_KEYS if getattr(inp, k) is not None}


def _list(ctx: ResolvedCtx, inp: ListInput) -> dict:
    found = apps.pour_membre(inp.org_id, ctx.sub)
    return {"apps": [{k: v for k, v in a.items() if k not in ("operations", "scope")}
                     for a in found]}


def _get(ctx: ResolvedCtx, inp: OneInput) -> dict:
    return _vue(ctx, _app(inp.org_id, inp.account))


def _org_card(org_id: int, account: str) -> Optional[dict]:
    """The org's existing Custom API card of this name: its non-secret connection
    fields and its saved definition — or None."""
    cred = credentials_store.get_credential_with_meta(
        credentials_store.ORG, str(org_id), store.CONNECTOR, account)
    if not cred:
        return None
    fields = credentials_store.unpack_secret(store.CONNECTOR, cred["secret"])
    try:
        d = ops_store.load_definition(credentials_store.ORG, str(org_id), account)
    except ops_store.InstanceMissing:
        d = ops_store.definition_of({})
    label = (cred.get("meta") or {}).get("label") or d.get("label")
    return {"bound": (cred.get("meta") or {}).get(store.META_KEY) is not None,
            "connection": {k: fields[k] for k in apps.CONNECTION_FIELDS if fields.get(k)},
            "definition": {**{k: v for k, v in d.items() if v not in (None, {}, [])},
                           **({"label": label} if label else {})}}


def _create(ctx: ResolvedCtx, inp: CreateInput) -> dict:
    try:
        account = apps.valider_nom(inp.account)
        card = _org_card(inp.org_id, account)
        if card is not None and not card["bound"] and not inp.adopt_org_key:
            raise AuthzDenied(409, "api_app_org_key_exists", (
                f"The org already has a Custom API card `{account}`: pass "
                "`adopt_org_key: true` to turn it into the app (its key becomes the "
                "app's org key), or choose another account."))
        if inp.adopt_org_key and (card is None or card["bound"]):
            raise AuthzDenied(404, "api_app_no_card_to_adopt",
                              f"No unbound org Custom API card `{account}` to adopt.")
        base = card["connection"] if inp.adopt_org_key else {}
        connection = apps.valider_connexion(inp.connection or {}, base=base)
        given = {**(card["definition"] if inp.adopt_org_key else {}),
                 **_definition_given(inp)}
        definition = apps.valider_definition(given, connection)
    except apps.AppRefused as e:
        raise _refus(e)
    try:
        app = store.creer(inp.org_id, account, connection, definition, ctx.sub)
    except store.NameTaken:
        raise AuthzDenied(409, "api_app_exists",
                          f"A Custom API app `{account}` already exists in this org.")
    if inp.adopt_org_key:
        credentials_store.update_meta(credentials_store.ORG, str(inp.org_id),
                                      store.CONNECTOR, account,
                                      {store.META_KEY: int(app["id"])})
    return {**_vue(ctx, app), "stale_keys": 0}


def _patch(ctx: ResolvedCtx, inp: PatchInput) -> dict:
    app = _app(inp.org_id, inp.account)
    old = app["connection"] or {}
    try:
        connection = (apps.valider_connexion(inp.connection, base=old)
                      if inp.connection is not None else old)
        definition = apps.valider_definition(_definition_given(inp), connection)
    except apps.AppRefused as e:
        raise _refus(e)
    saved = store.modifier(int(app["id"]), definition=definition, by=ctx.sub,
                           connection=connection if inp.connection is not None else None)
    if saved is None:
        raise AuthzDenied(404, "api_app_not_found",
                          f"No Custom API app `{inp.account}` in org #{inp.org_id}.")
    # Même prédicat qu'à l'appel (`apps.destination`) : ce que compte `stale_keys` est
    # exactement ce qui refusera `api_app_key_stale`.
    moved = apps.destination(old) != apps.destination(connection)
    stale = len(store.cles_liees(int(app["id"]))) if moved else 0
    return {**_vue(ctx, saved), "stale_keys": stale}


def _import(ctx: ResolvedCtx, inp: ImportInput) -> dict:
    app = _app(inp.org_id, inp.account)
    try:
        cands, skipped = ops_store.import_candidates(inp.source, url=inp.url,
                                                     document=inp.document)
    except ops_store.ImportFailed as e:
        raise AuthzDenied(400, "import_failed", str(e))
    return {"account": app["account"], "operations": cands, "skipped": skipped}


def _remove(ctx: ResolvedCtx, inp: OneInput) -> dict:
    app = _app(inp.org_id, inp.account)
    store.archiver(int(app["id"]), ctx.sub)
    return {"ok": True, "keys_left": len(store.cles_liees(int(app["id"])))}


_NOT_FOUND = DeclaredError(404, "api_app_not_found", "no live Custom API app of this "
                           "account in the org")
_CONNECTION = DeclaredError(400, "invalid_app_connection",
                            "the connection breaks the card's rules (unknown field, mode "
                            "outside the closed set, a field the mode requires missing, "
                            "base_url not an API root)")
_EGRESS = DeclaredError(400, "egress_refused",
                        "base_url or token_url resolves to an internal address not declared")
_DEFINITION = DeclaredError(400, "invalid_operations",
                            "the definition breaks the format")

CAPABILITIES += [
    Capability(key="custom_api.apps.list", handler=_list, Input=ListInput, Output=ApiApps,
               authz=ORG_MEMBER_OF("org_id"), mcp=None,
               description="The org's Custom API apps, each with the caller's access.",
               rest=RestBinding("GET", _PATH, _ID)),
    Capability(key="custom_api.apps.get", handler=_get, Input=OneInput, Output=ApiApp,
               authz=ORG_MEMBER_OF("org_id"), mcp=None,
               description=("One Custom API app: connection, definition, the caller's "
                            "access; its keys for an org admin. Never a secret."),
               errors=(_NOT_FOUND,), rest=RestBinding("GET", _ONE, _ID)),
    Capability(key="custom_api.apps.create", handler=_create, Input=CreateInput,
               Output=ApiAppSaved, authz=ORG_ADMIN_OF("org_id"), mcp=None,
               description=("Create a Custom API app (org admin) — or adopt the org's "
                            "existing card of that account."),
               errors=(_CONNECTION, _EGRESS, _DEFINITION,
                       DeclaredError(400, "invalid_app_account",
                                     "`account` is not a valid app id"),
                       DeclaredError(409, "api_app_exists",
                                     "a live app of this account exists"),
                       DeclaredError(409, "api_app_org_key_exists",
                                     "the org has a Custom API card of this account: "
                                     "adopt it (`adopt_org_key`) or choose another"),
                       DeclaredError(404, "api_app_no_card_to_adopt",
                                     "`adopt_org_key` without an unbound org card")),
               rest=RestBinding("POST", _PATH, _ID)),
    Capability(key="custom_api.apps.update", handler=_patch, Input=PatchInput,
               Output=ApiAppSaved, authz=ORG_ADMIN_OF("org_id"), mcp=None,
               description=("Change a Custom API app's connection or definition (org "
                            "admin; only the parts given)."),
               errors=(_NOT_FOUND, _CONNECTION, _EGRESS, _DEFINITION),
               rest=RestBinding("PATCH", _ONE, _ID)),
    Capability(key="custom_api.apps.import", handler=_import, Input=ImportInput,
               Output=ApiAppImport, authz=ORG_ADMIN_OF("org_id"), mcp=None,
               description=("Candidate actions for a Custom API app from an OpenAPI "
                            "document, without saving (org admin)."),
               errors=(_NOT_FOUND, DeclaredError(400, "import_failed",
                                                 "the document cannot be read")),
               rest=RestBinding("POST", _ONE + "/import", _ID)),
    Capability(key="custom_api.apps.remove", handler=_remove, Input=OneInput,
               Output=ApiAppRemoved, authz=ORG_ADMIN_OF("org_id"), mcp=None,
               description="Retire a Custom API app (org admin); its keys then refuse.",
               errors=(_NOT_FOUND,), rest=RestBinding("DELETE", _ONE, _ID)),
]
