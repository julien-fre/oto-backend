"""Zoho CRM — generic CRUD over modules (Contacts, Leads, Deals, Accounts…).

Credential = OAuth2 (self-client) with 3 secrets: client_id + client_secret +
refresh_token → generic multi-field model (ADR 0011), resolved per call via
`access.resolve_credential("zoho", want="byo")` (the winning ENTITY, not just
the fields — oto#25 lot b2: it is used to mark a row rejected on a dead grant,
`ZohoAuthError` at refresh). byo_user (no platform quota: the credential IS
the grant). The access token is derived/cached in memory on the client side.

**Consolidated surface (ADR 0047 §Amendment, applied to the zoho connector)**: one tool
per business OBJECT, the verb as an `op` parameter — `zoho_record` (list/get/search/create/
update/delete, all scoped by `module`) and `zoho_note` (list/create on a record).
`zoho_modules` stays alone: it takes NO parameter (it enumerates the `module`s that
the other two consume) and depends on a distinct OAuth scope (settings vs data).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Literal, Optional

import requests
from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, providers, status_hints
from ..auth import zoho as zoho_oauth
from ..connectors import flow as connector_flow
from ..connectors import health as connector_health
from ..connectors import verify as connector_verify

# Standard CRM modules probed to prove a real READ scope (at least one
# `ZohoCRM.modules.<m>.READ`). We stop at the first readable one; all in scope-mismatch =
# the token authenticates but has no CRM access (often a key from ANOTHER Zoho
# product — Analytics/Desk). Order = from most to least universally present.
_CRM_PROBE_MODULES = ("Contacts", "Deals", "Accounts", "Leads")


# Zoho hosts per regional data center; the self-client (client_id/secret) AND the
# refresh token are bound to their issuing region — a `.eu` self-client hitting
# `accounts.zoho.com` is rejected by Zoho with an opaque `invalid_client`. The
# credential's `data_center` field selects the API/OAuth domains. Recognized regions:
_DC_DOMAINS = {
    "com": ("https://www.zohoapis.com", "https://accounts.zoho.com"),
    "eu": ("https://www.zohoapis.eu", "https://accounts.zoho.eu"),
    "in": ("https://www.zohoapis.in", "https://accounts.zoho.in"),
    "au": ("https://www.zohoapis.com.au", "https://accounts.zoho.com.au"),
    "jp": ("https://www.zohoapis.jp", "https://accounts.zoho.jp"),
    "ca": ("https://www.zohoapis.ca", "https://accounts.zohocloud.ca"),
}


def _resolve_dc_domains(data_center: Optional[str]) -> tuple[str, str]:
    """`(api_domain, accounts_url)` for the declared Zoho region. Missing or
    unrecognized region → actionable `McpError`, **never** a silent fallback to `com` (that
    fallback masked the real cause of an `invalid_client`: self-client created on another
    region). `com` remains fully valid — we force no region, we just require a
    recognized choice."""
    dc = (data_center or "").strip().lower()
    if dc not in _DC_DOMAINS:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=(
            (f"Unrecognized Zoho data center: {data_center!r}." if dc
             else "Zoho data center missing.")
            + " Set your region in the \"Data center\" field of the Zoho connector —"
            " one of: com, eu, in, au, jp, ca. It is visible in the URL when you are"
            " logged in to Zoho (e.g. crm.zoho.eu → \"eu\", crm.zoho.com → \"com\")."
        )))
    return _DC_DOMAINS[dc]


def _credential_state_for(connector: str):
    """SINGLE SOURCE of "is this Zoho credential usable?", per connector.

    TWO-step connection: we set up the app (client_id + client_secret), then we
    consent — and it is the consent that produces the refresh_token. The
    intermediate state is therefore NORMAL, not a failure. One single label, rendered as-is
    by all surfaces (card verdict, "test the connection" probe).

    ⚠️ Consent fills in ONLY what OAuth produces (`zoho_oauth.PERSISTED_FIELDS`):
    the connector's OTHER required fields remain to be entered. Analytics thus requires an
    `org_id` that no flow can guess — it is the identifier of the user's Analytics org.
    We therefore derive what is missing from the REGISTRY rather than naming a field
    in code: coding `refresh_token` alone let an unusable Analytics credential pass as
    "complete", and every new required field would reopen the same hole.

    The fields produced by the flow are EXCLUDED from this check: they already have their
    own, more precise diagnostic (`_resolve_dc_domains` on the region). Two messages for a
    single problem are worth less than one good one."""
    def _state(fields: dict) -> status_hints.CredentialState:
        if fields.get("client_id") and fields.get("client_secret") \
                and not fields.get("refresh_token"):
            return status_hints.CredentialState(
                complete=False, missing=("refresh_token",),
                next_action=("Zoho app registered, but authorization has not been "
                             "granted yet — click \"Authorize oto with Zoho\" on the "
                             "connector card. (Or paste a refresh token if you "
                             "use a self client.)"))
        con = providers.REGISTRY.get(connector)
        manquants = tuple(f for f in (con.secret_fields if con else ())
                          if f.required and not fields.get(f.name)
                          and f.name not in zoho_oauth.PERSISTED_FIELDS)
        if manquants:
            libelles = ", ".join(f"« {f.label} »" for f in manquants)
            return status_hints.CredentialState(
                complete=False, missing=tuple(f.name for f in manquants),
                next_action=(f"{libelles} missing on the connector card — "
                             "the Zoho authorization cannot guess it."))
        return status_hints.CredentialState(complete=True)
    return _state


def _zoho_error_hint(exc: Exception) -> str:
    """Turn the raw Zoho OAuth error into an actionable message for the probe."""
    low = str(exc).lower()
    if "invalid_client" in low or "invalid_client_secret" in low:
        return ("incorrect client_id / client_secret or data center — the Zoho "
                "self-client is bound to its region, check the \"data center\" field.")
    if "invalid_code" in low or "invalid_grant" in low or "invalid_oauthtoken" in low:
        return "refresh token expired or revoked — regenerate it in the Zoho console."
    return f"Zoho connection failed: {exc}"


def _pending_action_for(connector: str):
    """Build the `status_hints` hook of a Zoho connector — the seam passes
    `(sub, org, group, entry)` without the connector name, we capture it here.

    TWO-step connection (server-based mode): the app is set up (client_id +
    client_secret) but consent has not been given yet → no
    refresh_token. Without this hook the card would look configured and fail on the
    first call; with it, the front shows the missing step."""
    def _hook(sub: str, org, group, entry: dict):  # noqa: ARG001
        if entry.get("mode") == "forbidden":
            return None   # nothing set up → the "to connect" verdict is enough
        try:
            # `resolve_credential(sub=…)`: the hook runs from /api/me (REST),
            # outside the MCP context → the sub must be EXPLICIT. `emit_on_failure=False`
            # (display probe, does not skew the usage signal).
            f = access.resolve_credential(
                connector, want="byo", sub=sub, emit_on_failure=False).fields
        # noqa: SILENT — display probe: without a credential, no pending action to offer
        except Exception:  # noqa: BLE001 — fail-open, never /api/me in error
            return None
        st = _credential_state_for(connector)(f)
        # The CTA label follows the missing step: offering "Authorize oto with
        # Zoho" to someone who already consented but is missing a field would send
        # them to redo the gesture that just succeeded.
        if st.complete:
            return None
        return ("Authorize oto with Zoho" if "refresh_token" in (st.missing or ())
                else st.next_action)
    return _hook


# The 3 Zoho connectors share this connection mode — registered here, this
# module being loaded unconditionally by `register_all`.
def _start_flow(ctx, connector: str, values: dict) -> dict:
    """Entry point of the generic flow — same body as the `me.zoho_connect` capability,
    whose handler it shares so that there is only ONE way to start."""
    from ..auth import flow as oauth_flow
    from ..capabilities import zoho_connect
    # `app` is a HIDDEN key, not a declared `FlowParam`: the front passes it outside the
    # form (the client knows who it is), it must never become a visible field.
    # Resolved HERE, once, against the closed list — never signed raw.
    return zoho_connect.start_for(
        ctx, connector, (values.get("data_center") or "").lower(),
        oauth_flow.resolve_return_app(values.get("app")))


for _c in ("zoho", "zohodesk", "zohoanalytics"):
    status_hints.register(_c, _pending_action_for(_c))
    status_hints.register_state(_c, _credential_state_for(_c))
    # The consent flow, declared like the rest: the front derives a region select
    # + a button from it, without knowing that "zoho" exists. The regions live HERE and
    # nowhere else — they used to be copied even into a registry label
    # that announced a data center the code rejects.
    connector_flow.declare(
        _c,
        start=lambda ctx, values, _c=_c: _start_flow(ctx, _c, values),
        label="Authorize oto with Zoho",
        callback_path="/api/zoho/oauth/callback",
        # Without region: "an app exists somewhere" (their own, their org's,
        # or oto's for at least one region). The region is only chosen at click time,
        # so the promise displayed before the click cannot depend on it.
        app_ready=lambda sub, _c=_c: zoho_oauth.has_app(_c, sub),
        params=(connector_flow.FlowParam(
            name="data_center", label="Region of your Zoho account", default="eu",
            help="the OAuth app and the token are bound to their data center",
            options=tuple((dc, lbl) for dc, lbl in (
                ("eu", "Europe (zoho.eu)"), ("com", "International (zoho.com)"),
                ("in", "India (zoho.in)"), ("au", "Australia (zoho.com.au)"),
                ("jp", "Japan (zoho.jp)"), ("ca", "Canada (zohocloud.ca)"),
            ) if dc in _DC_DOMAINS)),
        ),
    )


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: probe contract, unused here)
    """Probe WITHOUT side effects, in TWO steps (auth THEN scope):

    1. **OAuth token refresh**: validates client_id + client_secret + refresh_token +
       data_center in one go (failure → actionable message via `_zoho_error_hint`);
    2. **real read of a CRM module** (`GET /crm/v7/<module>?per_page=1`): a token
       can authenticate yet have NO CRM scope (e.g. a Zoho **Analytics** key set
       by mistake on the CRM connector — experienced 2026-07-04, the auth-only probe gave a
       false "ok"). If ALL modules return `OAUTH_SCOPE_MISMATCH`, we raise
       including the **scope actually granted** (returned by the refresh) → immediately
       diagnosable. No side effects (`per_page=1` reads).

    `_resolve_dc_domains` already raises a clear `McpError` if the region is missing/unknown.
    """
    from oto.tools.zoho.client import ZohoClient

    status_hints.require_complete("zoho", fields)
    api_domain, accounts_url = _resolve_dc_domains(fields.get("data_center"))

    # 1) auth — raw refresh: validates the 4 fields AND captures the granted `scope` (the
    # Zoho refresh returns it), for an actionable scope message if needed.
    try:
        # ⚠️ `data=` and NEVER `params=`: in a query string, client_id/client_secret/
        # refresh_token end up in the URL — hence in the message of any
        # requests exception (ConnectionError, HTTPError…), which is returned here to
        # the agent and logged. Leak experienced (#284); see `oto.tools.zoho.auth`.
        tok = requests.post(f"{accounts_url}/oauth/v2/token", data={
            "grant_type": "refresh_token",
            "client_id": fields.get("client_id"),
            "client_secret": fields.get("client_secret"),
            "refresh_token": fields.get("refresh_token"),
        }, timeout=20).json()
    except Exception as e:  # noqa: BLE001 — network / unreadable response
        raise ValueError(f"Zoho connection failed: {type(e).__name__}") from e
    if "access_token" not in tok:
        raise ValueError(_zoho_error_hint(tok.get("error") or tok))
    granted = tok.get("scope", "")

    # 2) scope — real READ through the SAME path as the `zoho_record` tool
    # (`list_records` adds the default `fields`, required in API v7): at least one
    # readable CRM module = usable credential. `per_page=1`, no side effects.
    client = ZohoClient(
        client_id=fields.get("client_id"), client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"),
        api_domain=api_domain, accounts_url=accounts_url,
    )
    scope_missing = False
    for module in _CRM_PROBE_MODULES:
        try:
            client.list_records(module, page=1, per_page=1)
            return  # real read OK → usable credential
        except McpError:
            raise
        # noqa: SILENT — scope missing for THIS module ⇒ try the next one, verdict given at the end
        except Exception as e:  # noqa: BLE001 — the provider error IS the probe's return
            if "OAUTH_SCOPE_MISMATCH" in str(e):
                scope_missing = True   # scope missing for THIS module — try the next one
            # other error (module disabled, INVALID_MODULE…) → try the next one
    if scope_missing:
        extra = f" (granted scope: {granted})" if granted else ""
        raise ValueError(
            "the token authenticates but has no CRM read scope" + extra
            + " — it may be a key from another Zoho product (Analytics/Desk). "
            "Regenerate a Zoho CRM self-client with ZohoCRM.modules.ALL "
            "(or leads/contacts/deals/accounts.READ).")
    raise ValueError("Zoho connection established but no readable CRM module "
                     "(modules disabled or inaccessible).")


def _demarque_apres_refresh(rc):
    """Unmark the vault row when the Zoho refresh SUCCEEDS (oto#25 lot b3).

    Symmetric to the Salesforce wiring, which did not exist here: unmarking
    excluded Zoho, for lack of knowing when a credential starts working again. A
    row marked rejected stayed red until a manual re-set, even after
    its owner had repaired their application.

    ⚠️ The callback is only invoked after a SUCCESSFUL refresh, and never on a cache
    hit (guaranteed on the oto-core side): that is what makes it proof of life and
    not stale information. The Zoho cache lasts an hour — a valid token
    proves a refresh from an hour ago, not a healthy credential right now.

    At MODULE level, outside `register()`: a factory locked inside the closure
    can only be exercised by mounting the whole connector, and this is precisely the kind
    of piece we want to be able to attack on its own.
    """
    if rc.entity_type is None:
        return None              # platform grant: no vault row to mark

    def _demarque(_token_data: dict) -> None:
        connector_health.record_health(
            "zoho", (rc.entity_type, rc.entity_id, rc.account), True, None)

    return _demarque


def register(mcp: FastMCP) -> None:
    connector_verify.register("zoho", _verify)
    from oto.tools.zoho import ZohoAuthError
    from oto.tools.zoho.client import ZohoClient

    def _client() -> tuple[ZohoClient, "access.ResolvedCredential"]:
        # `resolve_credential(want="byo")` — not `resolve_credential_fields`, which is
        # only a thin view of it (same fields, identical user > group > org cascade,
        # see `access/views.py`) — because we need the winning ENTITY in order to
        # mark a rejected row (oto#25 lot b2, `rc` returned to the caller).
        rc = access.resolve_credential("zoho", want="byo")
        creds = rc.fields
        # TWO-step connection: the app can be set up without consent having been
        # given (no refresh_token). Going ahead anyway produced an opaque OAuth failure
        # on the first call; we return the MISSING STEP, with the single label of
        # `_zoho_credential_state` — the same as the card's and the probe's.
        state = _credential_state_for("zoho")(creds)
        if not state.complete:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=state.next_action))
        api_domain, accounts_url = _resolve_dc_domains(creds.get("data_center"))
        client = ZohoClient(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            refresh_token=creds.get("refresh_token"),
            api_domain=api_domain,
            accounts_url=accounts_url,
            on_refresh=_demarque_apres_refresh(rc),
        )
        return client, rc

    @contextmanager
    def _marks_rejection(rc):
        """On `ZohoAuthError` (REFRESH refusal — dead grant; never a bare 401 from an
        ordinary application gesture, with an otherwise healthy key — see
        `oto.tools.zoho.auth`, the only place that raises it), mark the VAULT row
        actually served as rejected (`connectors.health.mark_rejected`, same scope
        guard as `verify`), THEN RE-RAISE — marking is never a fallback that
        swallows the real error (oto#25 lot b2)."""
        try:
            yield
        except ZohoAuthError as e:
            connector_health.mark_rejected(
                rc.entity_type, rc.entity_id, "zoho", rc.account, str(e))
            raise

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _need(value, name: str, op: str):
        """Required argument for THIS op — actionable error, never a fallback."""
        if value is None:
            raise _bad(f"op='{op}' requires {name}")
        return value

    @mcp.tool()
    def zoho_modules() -> dict:
        """List the available CRM modules (Contacts, Leads, Deals, Accounts…).

        Reads Zoho's module metadata, which needs the **settings** scope on the
        self-client — `ZohoCRM.settings.modules.READ` (or `ZohoCRM.settings.ALL`).
        A token minted with only data scopes (`ZohoCRM.modules.ALL`) reads records
        fine (`zoho_record`) but is rejected here; regenerate the self-client with
        the settings scope added.
        """
        from oto.tools.common.errors import UpstreamHTTPError
        client, rc = _client()
        try:
            with _marks_rejection(rc):
                return {"modules": client.list_modules()}
        except UpstreamHTTPError as e:
            if "OAUTH_SCOPE_MISMATCH" in str(e.body):
                raise McpError(ErrorData(code=INVALID_PARAMS, message=(
                    "the Zoho token lacks the metadata scope `ZohoCRM.settings.modules.READ` "
                    "(or `ZohoCRM.settings.ALL`) required to list modules — the data "
                    "(`zoho_record`) remains readable. Regenerate the Zoho CRM self-client "
                    "adding this settings scope alongside the data scopes.")))
            raise

    @mcp.tool()
    def zoho_record(
        module: str,
        op: Literal["list", "get", "search", "create", "update", "delete"] = "list",
        record_id: Optional[str] = None,
        data: Optional[dict] = None,
        criteria: Optional[str] = None,
        page: int = 1,
        per_page: int = 200,
        fields: Optional[str] = None,
    ) -> dict:
        """A CRM record inside a module — list, read, search, create, update, delete.

        `op`:
        - **"list"** (default): list records from a module. Paginated
          (`page` / `per_page`).
        - **"get"**: get one record by id (`record_id`). {} if not found.
        - **"search"**: search records. `criteria` = Zoho criteria, e.g.
          "(Email:equals:a@b.com)" or "(Last_Name:starts_with:Dup)". Paginated.
        - **"create"**: create a record in a module (`data` = field → value).
        - **"update"**: update a record's fields (`record_id` + `data`).
        - **"delete"**: delete a record. Irreversible.

        Args:
            module: e.g. "Contacts", "Leads", "Deals", "Accounts".
            op: list (default) | get | search | create | update | delete.
            record_id: op="get"/"update"/"delete" — the record id.
            data: op="create"/"update" — field → value.
            criteria: op="search" — Zoho criteria, e.g. "(Email:equals:a@b.com)"
                or "(Last_Name:starts_with:Dup)".
            page: pagination (list, search).
            per_page: page size (list, search).
            fields: op="list" — comma-separated field names. Optional — a sensible
                default set is used per known module if omitted.
        """
        client, rc = _client()

        with _marks_rejection(rc):
            if op == "list":
                return client.list_records(module, page=page, per_page=per_page,
                                           fields=fields)
            if op == "get":
                return client.get_record(module, _need(record_id, "record_id", op))
            if op == "search":
                return client.search_records(module, _need(criteria, "criteria", op),
                                             page=page, per_page=per_page)
            if op == "create":
                return client.create_record(module, _need(data, "data", op))
            if op == "update":
                return client.update_record(module, _need(record_id, "record_id", op),
                                            _need(data, "data", op))
            if op == "delete":
                return client.delete_record(module, _need(record_id, "record_id", op))
            raise _bad("op must be 'list', 'get', 'search', 'create', 'update' "
                       "or 'delete'")

    @mcp.tool()
    def zoho_note(
        module: str,
        record_id: str,
        op: Literal["list", "create"] = "list",
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> dict:
        """The notes attached to a CRM record.

        `op`:
        - **"list"** (default): list the notes attached to a record.
        - **"create"**: add a note to a record (`title` + `content`).

        Args:
            module: e.g. "Contacts", "Leads", "Deals", "Accounts".
            record_id: the record the notes are attached to.
            op: list (default) | create.
            title: op="create" — the note title.
            content: op="create" — the note body.
        """
        client, rc = _client()

        with _marks_rejection(rc):
            if op == "list":
                return {"notes": client.list_notes(module, record_id)}
            if op == "create":
                return client.create_note(module, record_id,
                                          _need(title, "title", op),
                                          _need(content, "content", op))
            raise _bad("op must be 'list' or 'create'")
