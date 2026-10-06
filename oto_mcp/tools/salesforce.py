"""Salesforce — generic CRUD over sObjects (Contact, Account…) via REST + SOQL.

Credential = OAuth2 Connected App with 3 secrets (client_id/client_secret/refresh_token)
+ non-secret `login_url` (login.salesforce.com prod, test.salesforce.com sandbox, or
My Domain) → generic multi-field model (ADR 0011), resolved per call via
`access.resolve_credential_fields("salesforce")`. byo_user OR byo_org (no platform
quota: the credential IS the grant). Unlike Zoho, no fixed region table: the
Salesforce refresh returns the `instance_url`, cached in memory on the client
side with the access token.

"Companies" = the standard **Account** sObject; contacts = **Contact**. Generic
surface per `sobject` (like hubspot/zoho) rather than dedicated contact/account
tools — also covers Lead/Opportunity/custom objects with no extra code.

**Consolidated surface (ADR 0047 §Amendment, applied to the salesforce connector)**:
one tool per business OBJECT, the verb as an `op` parameter — `salesforce_record`
(list/get/create/update/delete/upsert/bulk_create/bulk_update, all scoped by
`sobject`), `salesforce_query` (soql/sosl) and `salesforce_note` (list/create on
a record). The two scoping decisions:

- **the bulk ops are `op`s of `salesforce_record`**, not a separate tool: `items`
  is the plural of `data`, everything is scoped by the same `sobject`, and the
  "use me instead of N creates" warning then reads NEXT TO `op="create"`, where
  the agent looks for it. Real cost of the merge: two parameters (`items`,
  `all_or_none`), not a disjoint variant.
- **`salesforce_query` carries SOQL *and* SOSL**: two languages, but a single
  parameter, of the same shape (a string) and the same return — the criterion is
  parameter homogeneity, and here it is total. `op` names the language.

`salesforce_describe` stays **ALONE**: it describes a TYPE, not a record;
its output is a projected schema (not rows), its `verbose` has no counterpart
anywhere, and it is what enumerates the `fields` the others consume — same
discovery role as `zoho_modules` / `gmail_list_accounts`.

⚠️ **This module WRITES to the customer's CRM**: `salesforce_record` op=create /
update / delete / upsert / bulk_create / bulk_update, and `salesforce_note`
op=create. All `op` defaults are READS (`salesforce_record`
op="list", `salesforce_note` op="list", `salesforce_query` op="soql"): a call
without `op` can neither write nor delete.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, egress, status_hints
from ..connectors import flow as connector_flow
from ..connectors import health as connector_health
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Required argument for THIS op — an actionable error that NAMES the op and
    the argument, never a fallback.

    An EMPTY string counts as absent: `record_id=""` designates no
    record, and the URL built would target the COLLECTION — hence a different
    record than intended, or a delete that misses its target."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _bad(f"op='{op}' requires {name}")
    return value


def _login_url(login_url: Optional[str]) -> str:
    """The auth server to call — the SINGLE crossing point of both paths
    (probe and tools), hence the place where the egress guard bites exactly once.

    The default is a constant of this module: nothing to check. An ENTERED
    value, on the other hand, comes from an organization's card, and a `login_url`
    that resolves to the inside would send the refresh token — hence a secret — to
    a service on the machine (`oto_mcp/egress.py`)."""
    valeur = (login_url or "").strip().rstrip("/")
    if not valeur:
        return "https://login.salesforce.com"
    egress.check_url(valeur, connector="salesforce", field="login_url")
    return valeur


# What a field needs to be READ or WRITTEN — the rest of the 57 keys that
# Salesforce returns per field (aggregatable, byteLength, compoundFieldName, mask…)
# is of no use to the agent.
_DESCRIBE_FIELD_KEYS = ("name", "label", "type", "length", "nillable",
                        "createable", "updateable", "referenceTo", "defaultValue")
_DESCRIBE_OBJECT_KEYS = ("name", "label", "labelPlural", "custom", "createable",
                         "updateable", "deletable", "queryable", "searchable", "keyPrefix")


def _project_describe(raw: dict) -> dict:
    """Tightened projection of an sObject describe (signal #339).

    The raw payload of a standard Account is ~220 KB / 45 keys (127
    childRelationships, actionOverrides, recordTypeInfos…): too big for an
    agent's context, so truncated and spilled to a file by the client — hence
    unchainable, while only 51 fields matter. We keep the object + its fields,
    `verbose=True` returns the raw payload to whoever needs it."""
    fields = []
    for f in (raw.get("fields") or []):
        out = {k: f.get(k) for k in _DESCRIBE_FIELD_KEYS if f.get(k) not in (None, [], "")}
        out["name"] = f.get("name")          # always present, even if empty
        # A picklist is only useful as active API VALUES (the full object carries
        # label/validFor/defaultValue per entry = 4x the weight for nothing).
        picks = [p.get("value") for p in (f.get("picklistValues") or []) if p.get("active")]
        if picks:
            out["picklistValues"] = picks
        fields.append(out)
    obj = {k: raw.get(k) for k in _DESCRIBE_OBJECT_KEYS if raw.get(k) is not None}
    obj["fields"] = fields
    obj["field_count"] = len(fields)
    obj["_note"] = ("Projection (name/label/type/length/nillable/createable/updateable/"
                    "referenceTo/picklistValues). verbose=true for the raw Salesforce payload.")
    return obj


def _salesforce_credential_state(fields: dict) -> status_hints.CredentialState:
    """SINGLE SOURCE of "is this Salesforce credential usable?".

    Connection in TWO steps, like Zoho: we set the Connected App (Consumer Key +
    Secret + Login URL), then consent — and it is the consent that produces the
    refresh_token. The intermediate state is NORMAL, not an outage: without this
    declaration, `api_key_save` would probe a credential that is incomplete by
    construction, refuse the save, and the Connect button would become unreachable
    (the circular block experienced on Zoho on 28/07). A single label, rendered
    as-is by all surfaces."""
    if (fields.get("client_id") and fields.get("client_secret")
            and not fields.get("refresh_token")):
        return status_hints.CredentialState(
            complete=False, missing=("refresh_token",),
            next_action=("Connected App registered, but authorization has not "
                         "been given yet — click \"Connect\" on the connector's "
                         "card to open the Salesforce consent screen."))
    return status_hints.CredentialState(complete=True)


def _salesforce_pending_action(sub: str, org, group, entry: dict):  # noqa: ARG001
    """Missing step, for the card's verdict — the display COUNTERPART of
    `_salesforce_credential_state`.

    Both hooks are necessary and serve different moments: `register_state`
    says at SAVE time whether incompleteness is expected (otherwise the probe refuses
    to write), this one says at READ time what is left to do. Without it, the card
    looks configured — the app is set — and fails on the first tool call. Modeled on
    `zoho._pending_action_for`: same seam, same fail-open, same single label rendered
    as-is by all surfaces."""
    if entry.get("mode") == "forbidden":
        return None   # nothing set → the "to connect" verdict is enough
    try:
        # `resolve_credential(sub=…)`: the hook runs from /api/me (REST), outside
        # MCP context → the sub must be EXPLICIT. `emit_on_failure=False`: display
        # probe, it must not skew the usage signal.
        fields = access.resolve_credential(
            "salesforce", want="byo", sub=sub, emit_on_failure=False).fields
    # noqa: SILENT — display probe: without a credential, no pending action to offer
    except Exception:  # noqa: BLE001 — fail-open, never /api/me in error
        return None
    st = _salesforce_credential_state(fields)
    return None if st.complete else "Authorize oto at Salesforce"


status_hints.register_state("salesforce", _salesforce_credential_state)
status_hints.register("salesforce", _salesforce_pending_action)


def _start_flow(ctx, values: dict) -> dict:
    """Entry point of the generic flow — delegates to the SAME handler as the
    `me.salesforce_connect` capability, so there is only one way to start.

    `app` (like `scope`) is a hidden key, not a declared `FlowParam`: the
    dashboard/front passes it outside the form (the client knows who it is), it must
    never become a field visible to the user."""
    from ..capabilities import salesforce_connect
    return salesforce_connect.start_for(
        ctx, (values.get("scope") or "member"), values.get("app"))


# The consent flow, declared like Zoho's — this is what makes the button appear on the
# card, WITHOUT the dashboard having to know the name "salesforce".
# ⚠️ NO "For whom?" parameter. It existed, and it was a band-aid: the ORG surface
# had no connect button, so consenting for the org could only be done from the PERSONAL
# card, by declaring it in a menu. The missing lever was put in place (02/08) — the
# selector then became an absurd question: on your own card, you authorize for yourself;
# on the org's card, you authorize for the org. The scope is DEDUCED from the surface,
# the caller passes it (`values["scope"]`), it is no longer asked for.
connector_flow.declare(
    "salesforce",
    start=_start_flow,
    label="Authorize oto at Salesforce",
    callback_path="/api/salesforce/oauth/callback",
)


def _sf_error_hint(exc: Exception) -> str:
    """Translates the raw Salesforce OAuth error into an actionable message. Used
    by the `_verify` probe (credential already set) AND by the live OAuth flow
    (`salesforce_oauth.exchange_code`, authorization_code exchange failure) —
    both Salesforce error surfaces share the same raw vocabulary,
    so the same matching branches apply.

    ⚠️ A translation ADDS, it never REPLACES. The previous version
    substituted its guess for the provider's own words: an `invalid_grant` was
    systematically rendered as "stale refresh token, or wrong login_url", while
    Salesforce said something else (expired authorization code, call from
    an unauthorized IP…). The message blamed the wrong part and sent people to
    fix what worked — an hour lost on 31/07."""
    raw = " ".join(str(exc).split())[:220]
    low = raw.lower()
    hint = _sf_hint_for(low)
    return f"{hint} (Salesforce says: {raw})" if hint else (
        f"Salesforce connection failed: {raw}")


def _sf_hint_for(low: str) -> str:
    """The match alone — without the provider's own words, which the caller appends."""
    if "invalid_client" in low or "invalid_client_id" in low:
        return ("client_id / client_secret incorrect — check the Salesforce "
                "Connected App (Consumer Key / Consumer Secret).")
    if "invalid_grant" in low:
        return ("the grant was refused — token revoked or expired, authorization code "
                "already consumed, or call blocked by the app's IP restrictions "
                "(the refresh comes from OUR server, not from your browser). "
                "The exact reason is in parentheses below.")
    if "invalid_scope" in low:
        return ("the Connected App's OAuth Scopes don't include `api` and "
                "`refresh_token` (or `offline_access`) — Setup → App Manager → "
                "your app → Edit Policies → OAuth Scopes, then retry.")
    if "redirect_uri_mismatch" in low:
        # DERIVED, never hard-coded: this message is read from prod AND preprod, and
        # each sends its own redirect_uri. A hard-coded URL there always pointed to
        # prod — so a preprod user read "must be exactly <prod>"
        # while their backend sent something else. The message blamed the victim.
        from ..connectors import flow as connector_flow
        attendue = connector_flow.callback_url("salesforce") or "the URL shown on the card"
        return ("Connected App Callback URL incorrect — must be exactly "
                f"{attendue} (check there is no extra space or trailing slash).")
    return ""


def _verify(fields: dict, config: dict | None = None,
            instance: tuple | None = None) -> None:  # noqa: ARG001 (config: probe contract, unused here)
    """Two-step probe (auth THEN real access):

    1. **OAuth token refresh**: validates client_id + client_secret + refresh_token +
       login_url all at once (failure → actionable message via `_sf_error_hint`);
    2. **real read** (`SELECT Id FROM Contact LIMIT 1`): a token can
       authenticate but the Connected App's profile/permission set may not
       grant access to the Contact object — caught here rather than on the first agent call.

    ⚠️ **It is no longer "side-effect free", and cannot be.** Under rotation
    (RTR, imposed by Salesforce), step 1 consumes the refresh token and receives a
    new one: a probe that doesn't persist this replacement DESTROYS the connection it
    claims to verify. This is what happened on 31/07 — the post-write probe of
    `persist_token` killed the token 500 ms after it was set. It therefore wires the
    same persistence as the tools path, when it targets an already
    stored credential.
    """
    from oto.tools.salesforce.client import SalesforceClient

    client = SalesforceClient(
        client_id=fields.get("client_id"),
        client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"),
        login_url=_login_url(fields.get("login_url")),
        # `instance` = the key ACTUALLY probed, supplied by the caller. Without it we
        # can only guess via the cascade — which designates the nearest, not the one
        # being tested: a `verify level=org` for someone who ALSO has a personal key
        # compared the org token to the personal token, didn't recognize it, persisted
        # nothing, and thus killed the org token by refreshing it. Lived 03/08.
        on_refresh=_rotation_writer_for(fields.get("refresh_token") or "", instance),
    )
    try:
        client.query("SELECT Id FROM Contact LIMIT 1")
    except Exception as e:  # noqa: BLE001 — the provider error IS the probe's return
        raise ValueError(_sf_error_hint(e)) from e


class _Cible:
    """The probed entity, in the shape expected by `_rotation_writer`."""

    def __init__(self, entity_type, entity_id, account=""):
        self.entity_type, self.entity_id, self.account = entity_type, entity_id, account


def _rotation_writer_for(jeton_lu: str, instance: tuple | None = None):
    """The rotation writer for the PROBE.

    When the caller SAYS which entity it is testing (`instance`), we write there —
    without guessing. This is the nominal case, and the only correct one as soon as
    several keys exist for one connector: the cascade designates the NEAREST, not the one being probed.

    Otherwise (callers that don't supply it yet) we fall back to the cascade, and
    only wire the write if the resolved credential does carry the token we are
    about to consume. Two cases where we deliberately persist nothing:

    - **probe before persistence** (`api_key_save`): the tested fields are
      candidates, no row carries them yet — there is nothing to update;
    - **outside request context** (CLI, test): no org, hence no cascade.

    In both cases we fall back to the old behavior (no write), which
    is correct: we can't corrupt what we haven't identified.
    """
    from .. import access

    if instance is not None:
        etype, eid, *reste = instance
        return _rotation_writer(_Cible(etype, eid, reste[0] if reste else ""), jeton_lu)
    try:
        rc = access.resolve_credential("salesforce", emit_on_failure=False)
    # noqa: SILENT — declared debt: the refreshed token is not persisted (#424, verdict C)
    except Exception:  # noqa: BLE001 — no credential resolved = nothing to persist
        return None
    if rc.entity_type is None or (rc.fields or {}).get("refresh_token") != jeton_lu:
        return None
    return _rotation_writer(rc, jeton_lu)


def _rotation_writer(rc, jeton_lu: str):
    """Persists the RENEWED refresh token, where the old one was read.

    Salesforce enforces rotation (RTR) on External Client Apps: each
    refresh invalidates the token used and returns a new one. Not writing
    it amounts to revoking the connection on the first call — and getting it
    revoked *completely* on the second, Salesforce treating reuse of a
    consumed token as a compromise (revocation of the current token AND of the
    associated access tokens).

    ⚠️ **Conditional write**, not an overwrite: we only rewrite if the
    stored token is still the one we read. Two concurrent calls (or
    preprod, which shares this database with prod) may have rotated in the meantime;
    blindly overwriting would put back an already-consumed token, which is
    exactly the gesture Salesforce interprets as an attack.
    """
    from .. import credentials_store

    def _write(token_data: dict) -> None:
        # `on_refresh` is only invoked AFTER a SUCCESSFUL access-token refresh (never
        # on failure — see `oto.tools.salesforce.client`): it is the
        # "refresh succeeded" trigger of the unmarking (oto#25 lot b3), unconditional — whether or not there is
        # ROTATION of the refresh token, unlike the persistence below.
        if rc.entity_type is not None:
            connector_health.record_health(
                "salesforce", (rc.entity_type, rc.entity_id, rc.account), True, None)
        nouveau = token_data.get("refresh_token")
        # No rotation, or platform grant (no vault row to rewrite).
        if not nouveau or nouveau == jeton_lu or rc.entity_type is None:
            return
        row = credentials_store.get_credential_with_meta(
            rc.entity_type, rc.entity_id, "salesforce", account=rc.account)
        if not row or not row.get("secret"):
            return
        champs = credentials_store.unpack_secret("salesforce", row["secret"])
        if champs.get("refresh_token") != jeton_lu:
            return  # someone else already rotated: their value is more recent
        # ⚠️ `meta` MUST be passed again. The upsert does `meta = EXCLUDED.meta` with
        # `json.dumps(meta or {})`: omitting the argument is not "leave the
        # meta alone", it OVERWRITES it with {}. Since rotation rewrites on every tool
        # call, the previous version wiped `instance_url`/`identity_url`/
        # `connected_at` on first use — we then no longer knew which Salesforce
        # org the key pointed to. Spotted on 03/08, on a key that had rotated
        # since the day before while a fresh key still had its meta intact.
        credentials_store.set_credential(
            rc.entity_type, rc.entity_id, "salesforce",
            credentials_store.pack_secret("salesforce",
                                          {**champs, "refresh_token": nouveau}),
            account=rc.account, meta=row.get("meta") or {})

    return _write


# sObject Collections caps at 200 records per call — a HARD Salesforce
# limit (verified against the official docs), not an oto policy: we
# fail early on the tool side rather than let Salesforce return a 400.
_MAX_COLLECTION_RECORDS = 200


def _validate_bulk_items(items: list) -> None:
    if not items:
        raise _bad("items: at least one record required.")
    if len(items) > _MAX_COLLECTION_RECORDS:
        raise _bad(
            f"{len(items)} items — sObject Collections caps at "
            f"{_MAX_COLLECTION_RECORDS} per call, split into several calls."
        )


def _validate_update_items_have_id(items: list) -> None:
    for i, item in enumerate(items):
        if not item.get("Id"):
            raise _bad(f"items[{i}]: \"Id\" required to update a record.")


def _bulk_receipt(raw: list[dict]) -> dict:
    """Normalizes the sObject Collections response (list of {id, success, errors},
    same order as the items sent) into an indexed receipt — same spirit as the
    bulk receipt of folk_create, never N full response bodies."""
    results = [{"index": i, **r} for i, r in enumerate(raw)]
    return {"total": len(raw),
            "succeeded": sum(1 for r in raw if r.get("success")),
            "results": results}


# Ops of each tool, reads first. SINGLE SOURCE: the entry guard, the
# refusal message AND the schema enum (`Literal[…]` in the signature) derive from it — an
# added op therefore cannot be accepted without being announced (nor announced without being
# accepted). The read/write split is not decorative: it documents what a
# default `op` can reach (never a write).
_RECORD_READ_OPS = ("list", "get")
_RECORD_WRITE_OPS = ("create", "update", "delete", "upsert",
                     "bulk_create", "bulk_update")
_RECORD_OPS = _RECORD_READ_OPS + _RECORD_WRITE_OPS
_RECORD_OPS_ERROR = ("op must be 'list', 'get', 'create', 'update', 'delete', "
                     "'upsert', 'bulk_create' or 'bulk_update'")

_QUERY_OPS = ("soql", "sosl")            # both are READS
_QUERY_OPS_ERROR = "op must be 'soql' or 'sosl'"

_NOTE_READ_OPS = ("list",)
_NOTE_WRITE_OPS = ("create",)
_NOTE_OPS = _NOTE_READ_OPS + _NOTE_WRITE_OPS
_NOTE_OPS_ERROR = "op must be 'list' or 'create'"


def register(mcp: FastMCP) -> None:
    connector_verify.register("salesforce", _verify)
    from oto.tools.salesforce import SalesforceAuthError
    from oto.tools.salesforce.client import SalesforceClient

    def _client() -> tuple[SalesforceClient, "access.ResolvedCredential"]:
        # We go through `resolve_credential` (not `resolve_credential_fields`) because
        # we need the cascade's winning ENTITY: under rotation, the renewed token must be
        # rewritten exactly where it was read — member key, team key
        # or org key — otherwise we file it at the wrong level. The same entity
        # also serves to mark a rejected row (oto#25 lot b2, `rc` returned to the caller).
        rc = access.resolve_credential("salesforce")
        creds = rc.fields
        client = SalesforceClient(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            refresh_token=creds.get("refresh_token"),
            login_url=_login_url(creds.get("login_url")),
            on_refresh=_rotation_writer(rc, creds.get("refresh_token") or ""),
        )
        return client, rc

    @contextmanager
    def _marks_rejection(rc):
        """On `SalesforceAuthError` (REFRESH refusal — dead grant; never a bare 401
        from an application gesture on a specific record, with an otherwise
        healthy key — see `oto.tools.salesforce.client`, the only place that raises it), marks the
        VAULT row actually served as rejected (`connectors.health.mark_rejected`,
        same scope guard as `verify`), THEN RE-RAISES — marking is never a
        fallback that swallows the real error (oto#25 lot b2)."""
        try:
            yield
        except SalesforceAuthError as e:
            connector_health.mark_rejected(
                rc.entity_type, rc.entity_id, "salesforce", rc.account, str(e))
            raise

    @mcp.tool()
    def salesforce_describe(sobject: str, verbose: bool = False) -> dict:
        """Field metadata for an sObject type (e.g. "Account", "Contact", or custom).

        Returns a TIGHT projection: the object's own flags + one entry per field with
        what you need to read or write it (name, label, type, length, nillable,
        createable, updateable, referenceTo, picklist values). Salesforce's raw
        describe is ~220 KB for a standard Account (127 childRelationships,
        actionOverrides, recordTypeInfos, 57 keys per field) — too big to chain on.

        Args:
            sobject: e.g. "Account", "Contact", or a custom "Foo__c".
            verbose: True → the RAW Salesforce payload, unprojected. Only when you
                need something the projection drops (child relationships, layouts);
                expect it to be truncated by the client.
        """
        client, rc = _client()
        with _marks_rejection(rc):
            raw = client.describe(sobject)
        return raw if verbose else _project_describe(raw)

    @mcp.tool()
    def salesforce_record(
        sobject: str,
        op: Literal[_RECORD_OPS] = "list",
        record_id: Optional[str] = None,
        data: Optional[dict] = None,
        fields: Optional[str] = None,
        where: Optional[str] = None,
        limit: int = 200,
        external_id_field: Optional[str] = None,
        external_id: Optional[str] = None,
        items: Optional[list[dict]] = None,
        all_or_none: bool = False,
    ) -> dict:
        """A record of an sObject type — list, read, create, update, delete,
        upsert, or write up to 200 in one call.

        "Companies" are the standard **Account** sObject; contacts are
        **Contact**. The same surface covers Lead / Opportunity / any custom
        "Foo__c".

        `op`:
        - **"list"** (default): list records of `sobject`, built as a SOQL SELECT.
          Filter with `where`, pick columns with `fields`, cap with `limit`.
        - **"get"**: get one record by id (`record_id`).
        - **"create"** — ⚠️ WRITES: create a record (`data` = field → value), e.g.
          sobject="Contact", data={"FirstName": "Ada", "LastName": "Lovelace",
          "Email": "ada@example.com"}; sobject="Account", data={"Name": "Acme Corp"}.
        - **"update"** — ⚠️ WRITES: update a record's fields (`record_id` + `data`).
        - **"delete"** — ⚠️ WRITES: delete a record. Irreversible.
        - **"upsert"** — ⚠️ WRITES: create-or-update a record keyed on an external
          id field (idempotent).
        - **"bulk_create"** — ⚠️ WRITES: create up to 200 records of the SAME
          sObject type in ONE Salesforce call (sObject Collections) — instead of N
          separate op="create" calls. ⚠️ **Never lean on Salesforce's own
          de-duplication — here or on op="create"**: whether a duplicate rule fires
          at all is that org's Setup, not something oto applies or checks (measured:
          `success: true` on exact duplicates, same name and Account). A rule that
          DOES fire lands as a per-record `DUPLICATES_DETECTED` in `results`, not an
          exception — and records sent together are compared only with what is
          ALREADY in Salesforce, never with each other. Check existence yourself,
          and de-duplicate `items` against itself.
        - **"bulk_update"** — ⚠️ WRITES: update up to 200 records of the SAME
          sObject type in ONE Salesforce call (sObject Collections) — instead of N
          separate op="update" calls.

        Returns —
            The Salesforce payload for the single-record ops. For
            op="bulk_create"/"bulk_update": {"total", "succeeded", "results":
            [{"index", "id", "success", "errors"}, ...]} — ALWAYS inspect
            `results`: unlike op="create", a record can fail here with NO
            exception raised (the call itself succeeded, that one record didn't).

        Field metadata — what fields exist on an sObject, their type, whether they
        are createable/updateable, their picklist values — is a different tool:
        salesforce_describe.

        Args:
            sobject: e.g. "Contact", "Account" (companies), "Lead", "Opportunity",
                or a custom "Foo__c". For the bulk ops, EVERY record in `items`
                must be this type.
            op: list (default) | get | create | update | delete | upsert |
                bulk_create | bulk_update.
            record_id: op="get"/"update"/"delete" — the record id.
            data: op="create"/"update"/"upsert" — field → value.
            fields: op="list"/"get" — comma-separated field names. Optional — a
                sensible default set is used per known sObject if omitted.
            where: op="list" — SOQL WHERE clause without the "WHERE" keyword,
                e.g. "Industry = 'Technology'".
            limit: op="list" — maximum number of records (default 200).
            external_id_field: op="upsert" — name of the external id FIELD to key
                the create-or-update on.
            external_id: op="upsert" — the external id VALUE.
            items: op="bulk_create"/"bulk_update" — field→value dicts, same shape
                as `data`, one per record (max 200 — split into several calls
                above that). For op="bulk_update" each item MUST include "Id"
                (the record to update) plus the fields to change.
            all_or_none: op="bulk_create"/"bulk_update" — if true, the WHOLE batch
                rolls back when any record fails. Default false (Salesforce's own
                default): successes are kept, failures are reported per-record
                below.
        """
        # Refuse BEFORE any credential resolution: an unknown op never reaches
        # the client — hence never, via a derived path, a write.
        if op not in _RECORD_OPS:
            raise _bad(_RECORD_OPS_ERROR)
        client, rc = _client()

        with _marks_rejection(rc):
            # ---- reads ---------------------------------------------------------
            if op == "list":
                return client.list_records(sobject, fields=fields, where=where,
                                           limit=limit)
            if op == "get":
                return client.get_record(sobject, _need(record_id, "record_id", op),
                                         fields=fields)

            # ---- writes --------------------------------------------------------
            if op == "create":
                return client.create_record(sobject, _need(data, "data", op))
            if op == "update":
                return client.update_record(sobject,
                                            _need(record_id, "record_id", op),
                                            _need(data, "data", op))
            if op == "delete":
                return client.delete_record(sobject, _need(record_id, "record_id", op))
            if op == "upsert":
                return client.upsert_record(
                    sobject,
                    _need(external_id_field, "external_id_field", op),
                    _need(external_id, "external_id", op),
                    _need(data, "data", op))
            if op == "bulk_create":
                _validate_bulk_items(_need(items, "items", op))
                raw = client.create_records(sobject, items, all_or_none=all_or_none)
                return _bulk_receipt(raw)
            if op == "bulk_update":
                _validate_bulk_items(_need(items, "items", op))
                _validate_update_items_have_id(items)
                raw = client.update_records(sobject, items, all_or_none=all_or_none)
                return _bulk_receipt(raw)

            # Structurally unreachable (entry guard above) — safety net against
            # an implicit `return None` if an op were added to `_RECORD_OPS` without
            # its branch: better to refuse than to return "nothing" as a success.
            raise _bad(_RECORD_OPS_ERROR)

    @mcp.tool()
    def salesforce_query(query: str, op: Literal[_QUERY_OPS] = "soql") -> dict:
        """Run a raw statement against the org — SOQL query or SOSL search.

        `op`:
        - **"soql"** (default): a SOQL query, e.g.
          "SELECT Id, Name FROM Account WHERE Industry = 'Technology'".
        - **"sosl"**: a SOSL search, e.g.
          "FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name)".

        Both are reads. For a plain listing of one sObject you do not need to write
        SOQL at all: salesforce_record(op="list", sobject=…, where=…) builds it.

        Args:
            query: the statement itself — SOQL under op="soql", SOSL (a FIND …
                statement) under op="sosl". The two languages are NOT
                interchangeable: a FIND statement sent with the default op is
                rejected by Salesforce as a malformed query.
            op: soql (default) | sosl.
        """
        if op not in _QUERY_OPS:
            raise _bad(_QUERY_OPS_ERROR)
        client, rc = _client()
        with _marks_rejection(rc):
            if op == "soql":
                return client.query(_need(query, "query", op))
            if op == "sosl":
                return client.search(_need(query, "query", op))
            raise _bad(_QUERY_OPS_ERROR)   # unreachable, see salesforce_record

    @mcp.tool()
    def salesforce_note(
        record_id: str,
        op: Literal[_NOTE_OPS] = "list",
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> dict:
        """The Enhanced Notes attached to a record.

        Enhanced Notes = the **ContentNote** object, the Lightning default — NOT
        supported on orgs still on classic Notes.

        `op`:
        - **"list"** (default): list the notes attached to `record_id`.
        - **"create"** — ⚠️ WRITES: add an Enhanced Note to the record.

        Args:
            record_id: the record the notes are attached to (any sObject).
            op: list (default) | create.
            title: op="create" — the note title.
            body: op="create" — the note body.
        """
        if op not in _NOTE_OPS:
            raise _bad(_NOTE_OPS_ERROR)
        client, rc = _client()
        with _marks_rejection(rc):
            if op == "list":
                return {"notes": client.list_notes(record_id)}
            if op == "create":
                return client.create_note(record_id, _need(title, "title", op),
                                          _need(body, "body", op))
            raise _bad(_NOTE_OPS_ERROR)    # unreachable, see salesforce_record
