"""Snitcher — website visitor identification: which companies visit your site.

Wraps `oto.tools.snitcher.client.SnitcherClient` (REST v1, Bearer Personal
Access Token — dashboard → Settings → Account → API). keyed `api_key`,
byo-only: a PAT is bound to a Snitcher account, no platform key makes sense.
Rate limit 60 req/min per token.

**5 tools, one per business object** (silae, ADR 0047), covering all 27
endpoints of the official OpenAPI spec (fetched 2026-08-23):
- `snitcher_workspace` — account + workspace admin & reference data
  (op=list/get/me/segments/create/update/delete/invite/create_tag).
- `snitcher_organisation` — the identified companies
  (op=list/search/get/tag/untag).
- `snitcher_contact` — decision-makers at an organisation
  (op=list/reveal_email — reveal SPENDS a credit).
- `snitcher_session` — per-visit sessions & events; one tool, workspace-wide
  or narrowed to one organisation via `organisation_uuid`.
- `snitcher_custom_field` — field definitions AND per-organisation values
  (op=list/get/create/update/delete/values/set/set_many/clear).

**No param is silently ignored**: an op that doesn't use a provided argument
REFUSES instead of ignoring it (silae `_refuse_ignored`).

**Live-tested 2026-08-24** with a real trial token (workspace tulina.ai):
24 of 27 endpoints exercised — every read, the full tag cycle, the full
custom-field cycle (definitions + values, then cleaned up). NOT exercised:
reveal_email (spends a credit), workspace create/delete/invite. Two findings
that diverge from the published spec, both stamped on the oto-core client:
- **`op="search"` takes FLAT conditions only** — nested FilterGroups 422
  live despite the spec allowing them; and the accepted `field` set is
  visit-centric (last_seen, first_seen, tag, sessions, pageviews,
  time_on_site, url, referrer, source), NOT firmographics — filter by
  company name via op="list" `name=`, or use segments.
- **Response envelopes vary by endpoint** (top-level Laravel pagination on
  lists, bare objects on gets, empty bodies on deletes) — tools return them
  as-is.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Snitcher a rejeté le jeton (HTTP {status}) — vérifie le Personal Access "
                "Token posé sur ce connecteur (Snitcher : Settings → Account → API).")
    if status == 404:
        return f"Snitcher : ressource introuvable (HTTP 404) — {e.body}"
    if status == 422:
        return f"Snitcher : paramètres refusés (HTTP 422) — {e.body}"
    if status == 429:
        return ("Snitcher : trop de requêtes (429) — limite 60/minute par jeton. "
                "Réessaie dans un instant.")
    if status in (500, 502, 503, 504):
        return f"Snitcher est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Snitcher a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """« Tester la connexion » : GET /me, l'appel le plus léger et gratuit."""
    from oto.tools.snitcher.client import SnitcherClient
    SnitcherClient(api_key=fields["key"]).get_me()


def register(mcp: FastMCP) -> None:
    from oto.tools.snitcher.client import SnitcherClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("snitcher", _verify)

    def _client() -> SnitcherClient:
        key, _ = access.resolve_api_key("snitcher")
        return SnitcherClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Workspace — account-level admin & reference data
    # ================================================================

    @mcp.tool()
    def snitcher_workspace(
        op: Literal["list", "get", "me", "segments",
                     "create", "update", "delete", "invite", "create_tag"] = "list",
        workspace_uuid: Optional[str] = None,
        url: Optional[str] = None,
        usage_limit: Optional[int] = None,
        email: Optional[str] = None,
        tag_name: Optional[str] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
    ) -> object:
        """A Snitcher workspace (= one tracked website) — list yours, inspect
        one, read its segments, or administer it.

        Start here: op="list" gives the workspace uuids every other
        snitcher_* tool needs.

        Args:
            op: "list" (default, your workspaces) | "get" | "me" (the
                authenticated account profile) | "segments" (the workspace's
                saved segments — their uuids filter snitcher_organisation and
                snitcher_session) | "create" | "update" | "delete" | "invite" |
                "create_tag".
            workspace_uuid: REQUIRED by get/segments/update/delete/invite/
                create_tag. Refused on list/me/create.
            url: REQUIRED by "create" — the website the new workspace tracks.
            usage_limit: op="update" only — the only field the API lets you
                change.
            email: REQUIRED by "invite" — who to invite into the workspace.
            tag_name: REQUIRED by "create_tag" — declares a tag; attach it to
                a company with snitcher_organisation op="tag".
            page, size: op="list" only (size 1-1000).

        ⚠️ op="delete" DESTROYS the workspace and its collected visit
        history — irreversible, confirm with the user before calling it.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "op='list' ne prend que page/size",
                             workspace_uuid=workspace_uuid, url=url,
                             usage_limit=usage_limit, email=email, tag_name=tag_name)
            return _run(lambda: client.list_workspaces(page=page, size=size))
        if op == "me":
            _refuse_ignored(op, "op='me' ne prend aucun argument",
                             workspace_uuid=workspace_uuid, url=url, usage_limit=usage_limit,
                             email=email, tag_name=tag_name, page=page, size=size)
            return _run(client.get_me)
        if op == "create":
            _refuse_ignored(op, "un nouveau workspace n'a pas encore d'uuid",
                             workspace_uuid=workspace_uuid, usage_limit=usage_limit,
                             email=email, tag_name=tag_name, page=page, size=size)
            if not url:
                raise _bad("op='create' requiert `url`")
            return _run(lambda: client.create_workspace(url))
        if workspace_uuid is None:
            raise _bad(f"op={op!r} requiert `workspace_uuid`")
        if op == "get":
            _refuse_ignored(op, "op='get' ne prend que workspace_uuid",
                             url=url, usage_limit=usage_limit, email=email,
                             tag_name=tag_name, page=page, size=size)
            return _run(lambda: client.get_workspace(workspace_uuid))
        if op == "segments":
            _refuse_ignored(op, "op='segments' ne prend que workspace_uuid",
                             url=url, usage_limit=usage_limit, email=email,
                             tag_name=tag_name, page=page, size=size)
            return _run(lambda: client.list_segments(workspace_uuid))
        if op == "update":
            _refuse_ignored(op, "op='update' ne change que usage_limit",
                             url=url, email=email, tag_name=tag_name, page=page, size=size)
            if usage_limit is None:
                raise _bad("op='update' requiert `usage_limit` (le seul champ modifiable)")
            return _run(lambda: client.update_workspace(workspace_uuid, usage_limit=usage_limit))
        if op == "delete":
            _refuse_ignored(op, "une suppression ne prend que workspace_uuid",
                             url=url, usage_limit=usage_limit, email=email,
                             tag_name=tag_name, page=page, size=size)
            return _run(lambda: client.delete_workspace(workspace_uuid))
        if op == "invite":
            _refuse_ignored(op, "op='invite' ne prend que workspace_uuid + email",
                             url=url, usage_limit=usage_limit, tag_name=tag_name,
                             page=page, size=size)
            if not email:
                raise _bad("op='invite' requiert `email`")
            return _run(lambda: client.invite_user(workspace_uuid, email))
        if op == "create_tag":
            _refuse_ignored(op, "op='create_tag' ne prend que workspace_uuid + tag_name",
                             url=url, usage_limit=usage_limit, email=email,
                             page=page, size=size)
            if not tag_name:
                raise _bad("op='create_tag' requiert `tag_name`")
            return _run(lambda: client.create_workspace_tag(workspace_uuid, tag_name))
        raise _bad("op inconnu")

    # ================================================================
    # Organisation — the identified companies
    # ================================================================

    @mcp.tool()
    def snitcher_organisation(
        workspace_uuid: str,
        op: Literal["list", "search", "get", "tag", "untag"] = "list",
        organisation_uuid: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        segment_uuid: Optional[str] = None,
        date: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        name: Optional[str] = None,
        tag_name: Optional[str] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
    ) -> object:
        """Companies Snitcher identified visiting the workspace's website —
        list them, filter them, fetch one, or tag/untag one.

        Args:
            workspace_uuid: from snitcher_workspace op="list".
            op: "list" (default, simple filters) | "search" (advanced
                boolean filters) | "get" | "tag" | "untag".
            organisation_uuid: REQUIRED by get/tag/untag.
            filters: REQUIRED by "search" — {"operator": "AND"|"OR",
                "conditions": [{"field", "comparison", "value"?, "unit"?},
                ...]}. ⚠️ FLAT conditions only — nesting a group inside
                `conditions` is rejected live (422). Fields accepted (live
                -confirmed): last_seen, first_seen, tag, sessions, pageviews,
                time_on_site, url, referrer, source. Firmographics (name,
                industry, size…) are NOT filterable here — use op="list"
                with `name=`, or a segment. Comparisons: equal, not_equal,
                contains, not_contains, starts_with, ends_with,
                doesnt_start_with, doesnt_end_with, in, not_in, between,
                not_between, greater_than, less_than, greater_than_or_equal,
                less_than_or_equal, less_than_x_units_ago,
                more_than_x_units_ago (numeric value + unit
                second|minute|hour|day|week|month|year), set, not_set,
                is_true, is_false.
            segment_uuid: list/search — narrow to a saved segment
                (snitcher_workspace op="segments").
            date: op="list" only — companies that visited THAT day
                (YYYY-MM-DD). Mutually exclusive with date_from/date_to.
            date_from, date_to: op="list" only — a visit-date range.
            name: op="list" only — company-name contains-match.
            tag_name: REQUIRED by tag/untag.
            page, size: list/search pagination (size 1-1000).
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "utilise op='search' pour des filtres avancés, "
                             "op='get' pour une organisation précise",
                             organisation_uuid=organisation_uuid, filters=filters,
                             tag_name=tag_name)
            if date is not None and (date_from is not None or date_to is not None):
                raise _bad("`date` (un jour) et `date_from`/`date_to` (une plage) "
                           "sont mutuellement exclusifs")
            return _run(lambda: client.list_organisations(
                workspace_uuid, segment_uuid=segment_uuid, page=page, size=size,
                date=date, date_from=date_from, date_to=date_to, name=name))
        if op == "search":
            _refuse_ignored(op, "op='search' filtre via `filters`, pas par date/name",
                             organisation_uuid=organisation_uuid, date=date,
                             date_from=date_from, date_to=date_to, name=name,
                             tag_name=tag_name)
            if not filters:
                raise _bad("op='search' requiert `filters` (FilterGroup)")
            return _run(lambda: client.filter_organisations(
                workspace_uuid, filters, segment_uuid=segment_uuid, page=page, size=size))
        if organisation_uuid is None:
            raise _bad(f"op={op!r} requiert `organisation_uuid`")
        if op == "get":
            _refuse_ignored(op, "op='get' ne prend que workspace_uuid + organisation_uuid",
                             filters=filters, segment_uuid=segment_uuid, date=date,
                             date_from=date_from, date_to=date_to, name=name,
                             tag_name=tag_name, page=page, size=size)
            return _run(lambda: client.get_organisation(workspace_uuid, organisation_uuid))
        if op in ("tag", "untag"):
            _refuse_ignored(op, f"op={op!r} ne prend que `tag_name`",
                             filters=filters, segment_uuid=segment_uuid, date=date,
                             date_from=date_from, date_to=date_to, name=name,
                             page=page, size=size)
            if not tag_name:
                raise _bad(f"op={op!r} requiert `tag_name`")
            if op == "tag":
                return _run(lambda: client.add_organisation_tag(
                    workspace_uuid, organisation_uuid, tag_name))
            return _run(lambda: client.remove_organisation_tag(
                workspace_uuid, organisation_uuid, tag_name))
        raise _bad("op inconnu")

    # ================================================================
    # Contact — decision-makers at an identified organisation
    # ================================================================

    @mcp.tool()
    def snitcher_contact(
        workspace_uuid: str,
        op: Literal["list", "reveal_email"] = "list",
        organisation_uuid: Optional[str] = None,
        domain: Optional[str] = None,
        contact_uuid: Optional[str] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
    ) -> object:
        """Contacts (people) at an identified organisation — list them, or
        reveal one's email.

        ⚠️ op="reveal_email" SPENDS a Snitcher credit — it permanently
        un-hides that contact's address on the account. Confirm intent before
        calling it; listing is free.

        Args:
            workspace_uuid: from snitcher_workspace op="list".
            op: "list" (default) | "reveal_email".
            organisation_uuid: op="list" — the company whose contacts to
                fetch. Exactly one of organisation_uuid / domain.
            domain: op="list" — alternative to organisation_uuid: look the
                company up by its website domain.
            contact_uuid: REQUIRED by "reveal_email" (from a prior list).
            page, size: op="list" pagination (size 1-1000).
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "op='list' identifie la société par "
                             "organisation_uuid OU domain",
                             contact_uuid=contact_uuid)
            if (organisation_uuid is None) == (domain is None):
                raise _bad("op='list' requiert EXACTEMENT UN de `organisation_uuid` / `domain`")
            return _run(lambda: client.list_contacts(
                workspace_uuid, organisation_uuid=organisation_uuid, domain=domain,
                page=page, size=size))
        if op == "reveal_email":
            _refuse_ignored(op, "op='reveal_email' cible un contact précis",
                             organisation_uuid=organisation_uuid, domain=domain,
                             page=page, size=size)
            if not contact_uuid:
                raise _bad("op='reveal_email' requiert `contact_uuid`")
            return _run(lambda: client.reveal_contact_email(workspace_uuid, contact_uuid))
        raise _bad("op doit être 'list' ou 'reveal_email'")

    # ================================================================
    # Session — per-visit data with events
    # ================================================================

    @mcp.tool()
    def snitcher_session(
        workspace_uuid: str,
        organisation_uuid: Optional[str] = None,
        segment_uuid: Optional[str] = None,
        date: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        url: Optional[str] = None,
        referrer: Optional[str] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
    ) -> object:
        """Visit sessions — workspace-wide, or one organisation's history.

        Each session carries an `events` array: pageviews (with time_on_page),
        form submissions (WITH the submitted field values), custom `track`
        events, clicks and downloads. (The `views` array also present is
        deprecated — read `events`.)

        Args:
            workspace_uuid: from snitcher_workspace op="list".
            organisation_uuid: narrow to ONE company's sessions. Omitted =
                all sessions in the workspace — then `date` or `date_from`
                is REQUIRED by the API.
            segment_uuid: workspace-wide only (refused with
                organisation_uuid) — only sessions matching this segment.
            date: one day (YYYY-MM-DD). Mutually exclusive with
                date_from/date_to.
            date_from, date_to: a date range (date_to defaults to today).
            url: contains-match on the visited URL.
            referrer: contains-match on the referrer.
            page, size: pagination (size 1-1000).
        """
        client = _client()
        if date is not None and (date_from is not None or date_to is not None):
            raise _bad("`date` (un jour) et `date_from`/`date_to` (une plage) "
                       "sont mutuellement exclusifs")
        if organisation_uuid is not None:
            _refuse_ignored("sessions d'une organisation",
                             "`segment_uuid` ne s'applique qu'aux sessions workspace-wide",
                             segment_uuid=segment_uuid)
            return _run(lambda: client.list_organisation_sessions(
                workspace_uuid, organisation_uuid,
                date=date, date_from=date_from, date_to=date_to,
                url=url, referrer=referrer, page=page, size=size))
        return _run(lambda: client.list_sessions(
            workspace_uuid, segment_uuid=segment_uuid,
            date=date, date_from=date_from, date_to=date_to,
            url=url, referrer=referrer, page=page, size=size))

    # ================================================================
    # Custom fields — definitions + per-organisation values
    # ================================================================

    @mcp.tool()
    def snitcher_custom_field(
        workspace_uuid: str,
        op: Literal["list", "get", "create", "update", "delete",
                     "values", "set", "set_many", "clear"] = "list",
        key: Optional[str] = None,
        organisation_uuid: Optional[str] = None,
        name: Optional[str] = None,
        type: Optional[str] = None,  # noqa: A002
        description: Optional[str] = None,
        visible_in_spotter: Optional[bool] = None,
        field_rules: Optional[List[Dict[str, Any]]] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        value: Optional[Union[str, int, float, bool, List[Any]]] = None,
        values: Optional[Dict[str, Any]] = None,
    ) -> object:
        """Custom fields on organisations — the DEFINITIONS (workspace-level
        schema: list/get/create/update/delete) and the VALUES they carry on
        one organisation (values/set/set_many/clear).

        Args:
            workspace_uuid: from snitcher_workspace op="list".
            op: definitions — "list" (default) | "get" | "create" | "update" |
                "delete"; values — "values" (read an organisation's) | "set"
                (one field) | "set_many" (up to 50 at once) | "clear".
            key: the field's machine key. REQUIRED by get/update/delete/set/
                clear.
            organisation_uuid: REQUIRED by values/set/set_many/clear.
            name: REQUIRED by "create" (the human label); optional on
                "update".
            type: REQUIRED by "create" — the field's data type (e.g. "text").
                Immutable afterwards.
            description: create/update.
            visible_in_spotter: create/update — ⚠️ True exposes the field's
                values to any script on the tracked website. Off by default.
            field_rules: create/update — [{type, config?}, ...].
            options: create/update — select-type choices
                [{key, label, color?}, ...].
            value: REQUIRED by "set" — the value to write (non-empty).
            values: REQUIRED by "set_many" — {key: value, ...} (≤50 keys).
                Unknown keys are CREATED automatically, type inferred. Empty
                values are refused by the API — use op="clear" to remove one.

        ⚠️ op="delete" drops the definition AND its values on every
        organisation in the workspace.
        """
        client = _client()
        defs_hint = "les ops de DÉFINITION ne prennent pas d'organisation"
        if op == "list":
            _refuse_ignored(op, "op='list' ne prend que workspace_uuid",
                             key=key, organisation_uuid=organisation_uuid, name=name,
                             type=type, description=description,
                             visible_in_spotter=visible_in_spotter,
                             field_rules=field_rules, options=options,
                             value=value, values=values)
            return _run(lambda: client.list_custom_fields(workspace_uuid))
        if op == "create":
            _refuse_ignored(op, defs_hint,
                             organisation_uuid=organisation_uuid, value=value, values=values)
            if not name or not type:
                raise _bad("op='create' requiert `name` et `type`")
            return _run(lambda: client.create_custom_field(
                workspace_uuid, name, type, key=key, description=description,
                visible_in_spotter=visible_in_spotter,
                field_rules=field_rules, options=options))
        if op in ("get", "update", "delete"):
            if not key:
                raise _bad(f"op={op!r} requiert `key`")
            if op == "get":
                _refuse_ignored(op, defs_hint,
                                 organisation_uuid=organisation_uuid, name=name, type=type,
                                 description=description, visible_in_spotter=visible_in_spotter,
                                 field_rules=field_rules, options=options,
                                 value=value, values=values)
                return _run(lambda: client.get_custom_field(workspace_uuid, key))
            if op == "update":
                _refuse_ignored(op, "le `type` d'un champ est immuable ; " + defs_hint,
                                 organisation_uuid=organisation_uuid, type=type,
                                 value=value, values=values)
                return _run(lambda: client.update_custom_field(
                    workspace_uuid, key, name=name, description=description,
                    visible_in_spotter=visible_in_spotter,
                    field_rules=field_rules, options=options))
            _refuse_ignored(op, defs_hint,
                             organisation_uuid=organisation_uuid, name=name, type=type,
                             description=description, visible_in_spotter=visible_in_spotter,
                             field_rules=field_rules, options=options,
                             value=value, values=values)
            return _run(lambda: client.delete_custom_field(workspace_uuid, key))
        # ---- value ops: all need an organisation
        if organisation_uuid is None:
            raise _bad(f"op={op!r} requiert `organisation_uuid`")
        vals_hint = "les ops de VALEUR ne touchent pas la définition du champ"
        if op == "values":
            _refuse_ignored(op, vals_hint,
                             key=key, name=name, type=type, description=description,
                             visible_in_spotter=visible_in_spotter,
                             field_rules=field_rules, options=options,
                             value=value, values=values)
            return _run(lambda: client.list_custom_field_values(workspace_uuid, organisation_uuid))
        if op == "set":
            _refuse_ignored(op, vals_hint + " ; pour plusieurs champs, op='set_many'",
                             name=name, type=type, description=description,
                             visible_in_spotter=visible_in_spotter,
                             field_rules=field_rules, options=options, values=values)
            if not key or value is None:
                raise _bad("op='set' requiert `key` et `value`")
            return _run(lambda: client.set_custom_field_value(
                workspace_uuid, organisation_uuid, key, value))
        if op == "set_many":
            _refuse_ignored(op, vals_hint + " ; pour un seul champ, op='set'",
                             key=key, name=name, type=type, description=description,
                             visible_in_spotter=visible_in_spotter,
                             field_rules=field_rules, options=options, value=value)
            if not values:
                raise _bad("op='set_many' requiert `values` ({clé: valeur, …})")
            return _run(lambda: client.set_custom_field_values(
                workspace_uuid, organisation_uuid, values))
        if op == "clear":
            _refuse_ignored(op, vals_hint,
                             name=name, type=type, description=description,
                             visible_in_spotter=visible_in_spotter,
                             field_rules=field_rules, options=options,
                             value=value, values=values)
            if not key:
                raise _bad("op='clear' requiert `key`")
            return _run(lambda: client.clear_custom_field_value(
                workspace_uuid, organisation_uuid, key))
        raise _bad("op inconnu")
