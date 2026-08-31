"""Pipedrive CRM — deals, personnes, organisations, activités, notes, leads.

Wrappe `oto.tools.pipedrive.PipedriveClient`. Credential = **token API personnel**
(`api_token`) + `company_domain` facultatif (non secret, route vers le data center
du compte) → modèle générique multi-champs (ADR 0011), résolu par appel via
`access.resolve_credential_fields("pipedrive")`. byo_user OU byo_org, pas de clé
plateforme (le token EST le grant).

**Surface consolidée (ADR 0047 §Amendement)** : un tool par OBJET métier, le verbe
en paramètre `op` — 13 tools → 5. Le module portait DÉJÀ l'axe `entity` (comme
hubspot/salesforce) : deals/persons/organizations/activities/products/pipelines/
stages partagent les mêmes verbes en API v2, donc leur CRUD + leur schéma tiennent
dans un seul `pipedrive_record`. La **recherche** garde son tool
(`pipedrive_search`) : ses paramètres lui sont propres (`term`, `exact_match`,
`fields` = les champs INTERROGÉS, à ne pas confondre avec `include_fields`/
`custom_fields` du CRUD), et son `op` ne choisit pas un verbe mais la portée —
mono-entité ou transverse (`/itemSearch`). Ce que Pipedrive n'a pas porté en v2
garde son objet dédié (`pipedrive_note`, `pipedrive_lead`, `pipedrive_users`) — la
frontière est dans l'API (v1, pagination offset), autant l'assumer.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _domain(company_domain: Optional[str]) -> Optional[str]:
    return (company_domain or "").strip().strip(".") or None


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: contrat de sonde)
    """Sonde SANS effet de bord, en deux temps (auth PUIS accès réel) :

    1. `GET /users/me` : valide le token et le `company_domain` s'il est fourni
       (un sous-domaine erroné ne résout pas / renvoie 401) ;
    2. lecture réelle d'un deal : un token peut authentifier alors que
       l'utilisateur porteur n'a pas la permission « deals » — capté ici plutôt
       qu'au premier appel de l'agent.
    """
    from oto.tools.pipedrive.client import PipedriveClient

    client = PipedriveClient(
        api_token=fields.get("api_token"),
        company_domain=_domain(fields.get("company_domain")),
    )
    try:
        client.get_current_user()
    except Exception as e:  # noqa: BLE001 — l'erreur provider EST le retour de la sonde
        raise ValueError(
            f"token API Pipedrive refusé (Settings → Personal preferences → API) : {e}"
        ) from e
    try:
        client.list_records("deals", limit=1)
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"token valide, mais la lecture des deals est refusée (permissions du "
            f"profil Pipedrive) : {e}") from e


def register(mcp: FastMCP) -> None:
    connector_verify.register("pipedrive", _verify)
    from oto.tools.pipedrive.client import PipedriveClient

    def _client() -> PipedriveClient:
        creds = access.resolve_credential_fields("pipedrive")
        return PipedriveClient(
            api_token=creds.get("api_token"),
            company_domain=_domain(creds.get("company_domain")),
        )

    # ---- helpers de dispatch (patron `op=`, ADR 0047) --------------------

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _need(value, name: str, op: str):
        """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback."""
        if value is None:
            raise _bad(f"op='{op}' requiert {name}")
        return value

    # ---- les objets de l'API v2 : CRUD générique + schéma ----------------

    @mcp.tool()
    def pipedrive_record(
        entity: Literal["deals", "persons", "organizations", "activities",
                        "products", "pipelines", "stages"],
        op: Literal["list", "get", "create", "update", "delete", "fields"] = "list",
        record_id: Optional[int] = None,
        data: Optional[dict] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        owner_id: Optional[int] = None,
        person_id: Optional[int] = None,
        org_id: Optional[int] = None,
        pipeline_id: Optional[int] = None,
        stage_id: Optional[int] = None,
        status: Optional[str] = None,
        filter_id: Optional[int] = None,
        updated_since: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_direction: Optional[str] = None,
        include_fields: Optional[str] = None,
        custom_fields: Optional[str] = None,
    ) -> dict:
        """CRM records (API v2) — read, write and inspect the schema of one entity.

        `op` :
        - **"list"** (default): list records of an entity (cursor-paginated).
        - **"get"**: fetch one record by id (entity = deals | persons |
          organizations | …).
        - **"create"**: create a record.
        - **"update"**: update a record (partial — only the keys you pass are
          changed).
        - **"delete"**: delete a record.
        - **"fields"**: list an entity's fields — the only way to get custom field
          keys. Custom fields are keyed by a 40-char hash, both in responses and
          when writing. Call this first when a deal/person carries client-specific
          data. ⚠️ Narrower entity set than the other ops: deals | persons |
          organizations | products | activities (no pipelines / stages — Pipedrive
          has no fields endpoint for those).

        Args:
            entity: deals | persons | organizations | activities | products |
                pipelines | stages. Required for every op.
            op: list (default) | get | create | update | delete | fields.
            record_id: op="get"/"update"/"delete" — id of the record.
            data: op="create"/"update" — API v2 body — e.g. {"title": …, "value":
                1000, "currency": "EUR", "person_id": …} for a deal; {"name": …,
                "emails": [{"value": "a@b.c", "primary": true}]} for a person.
                Custom fields go under {"custom_fields": {"<hash>": value}}.
            limit: op="list"/"fields" — up to 500.
            cursor: op="list"/"fields" — `next_cursor` from a previous response
                (null = last page).
            owner_id / person_id / org_id / pipeline_id / stage_id: op="list" —
                restrict to records linked to that id (entity-dependent).
            status: op="list" — deals: open | won | lost.
            filter_id: op="list" — id of a saved Pipedrive filter.
            updated_since: op="list" — RFC 3339 timestamp — only records changed
                since.
            sort_by: op="list" — id | update_time | add_time (entity-dependent).
            sort_direction: op="list" — direction of `sort_by`.
            include_fields: op="list"/"get" — extra fields to include.
            custom_fields: op="list"/"get" — comma-separated custom field keys to
                include (their 40-char hashes come from op="fields").
        """
        if op == "list":
            filters = {
                "owner_id": owner_id, "person_id": person_id, "org_id": org_id,
                "pipeline_id": pipeline_id, "stage_id": stage_id, "status": status,
                "filter_id": filter_id, "updated_since": updated_since,
                "sort_by": sort_by, "sort_direction": sort_direction,
                "include_fields": include_fields, "custom_fields": custom_fields,
            }
            return _client().list_records(
                entity, limit=limit, cursor=cursor,
                **{k: v for k, v in filters.items() if v is not None})

        if op == "get":
            return _client().get_record(
                entity, _need(record_id, "record_id", op),
                include_fields=include_fields, custom_fields=custom_fields)

        if op == "create":
            return _client().create_record(entity, _need(data, "data", op))

        if op == "update":
            return _client().update_record(
                entity, _need(record_id, "record_id", op), _need(data, "data", op))

        if op == "delete":
            return _client().delete_record(
                entity, _need(record_id, "record_id", op))

        if op == "fields":
            return _client().list_fields(entity, limit=limit, cursor=cursor)

        raise _bad("op doit être 'list', 'get', 'create', 'update', 'delete' "
                   "ou 'fields'")

    # ---- recherche : dans une entité, ou transverse ----------------------

    @mcp.tool()
    def pipedrive_search(
        term: str,
        op: Literal["entity", "all"] = "entity",
        entity: Optional[Literal["deals", "persons", "organizations", "products",
                                 "leads"]] = None,
        item_types: Optional[str] = None,
        fields: Optional[str] = None,
        exact_match: bool = False,
        search_for_related_items: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
        person_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> dict:
        """Full-text search.

        `op` :
        - **"entity"** (default): search within ONE entity (`entity` required).
        - **"all"**: search across several object types at once.

        Args:
            term: at least 2 characters (1 if exact_match).
            op: entity (default) | all.
            entity: op="entity" — deals | persons | organizations | products |
                leads.
            item_types: op="all" — comma-separated — deal, person, organization,
                product, lead, file, mail_attachment, project (defaults to all).
            fields: comma-separated fields to search in (e.g. "name,email" on
                persons; defaults to every searchable field).
            exact_match: case-insensitive exact match instead of partial.
            search_for_related_items: op="all" — also return records linked to the
                matches.
            limit: page size.
            cursor: `next_cursor` from a previous response.
            person_id/organization_id: op="entity" — restrict deals to a linked
                record.
            status: op="entity" — deals only — open | won | lost.
        """
        if op == "entity":
            extra = {}
            if person_id is not None:
                extra["person_id"] = person_id
            if organization_id is not None:
                extra["organization_id"] = organization_id
            if status:
                extra["status"] = status
            return _client().search(
                _need(entity, "entity", op), term, fields=fields,
                exact_match=exact_match, limit=limit, cursor=cursor, **extra)

        if op == "all":
            return _client().search_all(
                term, item_types=item_types, fields=fields,
                exact_match=exact_match,
                search_for_related_items=search_for_related_items, limit=limit,
                cursor=cursor)

        raise _bad("op doit être 'entity' ou 'all'")

    # ---- notes (API v1) --------------------------------------------------

    @mcp.tool()
    def pipedrive_note(
        op: Literal["list", "create"] = "list",
        content: Optional[str] = None,
        deal_id: Optional[int] = None,
        person_id: Optional[int] = None,
        org_id: Optional[int] = None,
        lead_id: Optional[str] = None,
        limit: int = 100,
        start: int = 0,
    ) -> dict:
        """Notes attached to a deal / person / organization / lead.

        `op` :
        - **"list"** (default): list notes attached to a deal / person /
          organization / lead. Offset-paginated (`start`): notes are still on
          Pipedrive's v1 API.
        - **"create"**: attach a note (HTML accepted) to a deal / person /
          organization / lead. Exactly one target id is expected — a note with no
          target is rejected.

        Args:
            op: list (default) | create.
            content: op="create" — body of the note (HTML accepted).
            deal_id/person_id/org_id/lead_id: the linked record — filter on
                op="list", target on op="create".
            limit: op="list" — page size.
            start: op="list" — offset (v1 pagination).
        """
        if op == "list":
            return _client().list_notes(
                deal_id=deal_id, person_id=person_id, org_id=org_id,
                lead_id=lead_id, limit=limit, start=start)

        if op == "create":
            content = _need(content, "content", op)
            if not any([deal_id, person_id, org_id, lead_id]):
                raise _bad("op='create' requiert une cible : deal_id, person_id, "
                           "org_id ou lead_id")
            return _client().create_note(
                content, deal_id=deal_id, person_id=person_id, org_id=org_id,
                lead_id=lead_id)

        raise _bad("op doit être 'list' ou 'create'")

    # ---- leads (CRUD resté en API v1) ------------------------------------

    @mcp.tool()
    def pipedrive_lead(
        op: Literal["list", "create"] = "list",
        title: Optional[str] = None,
        owner_id: Optional[int] = None,
        person_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        filter_id: Optional[int] = None,
        archived_status: Optional[str] = None,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        expected_close_date: Optional[str] = None,
        limit: int = 100,
        start: int = 0,
    ) -> dict:
        """Leads (Pipedrive's pre-deal inbox).

        `op` :
        - **"list"** (default): list leads (Pipedrive's pre-deal inbox).
        - **"create"**: create a lead — requires person_id or organization_id.

        Args:
            op: list (default) | create.
            title: op="create" — title of the lead (required).
            owner_id/person_id/organization_id: the linked records — filters on
                op="list", links on op="create".
            filter_id: op="list" — id of a saved Pipedrive filter.
            archived_status: op="list" — archived | not_archived | all.
            amount/currency: op="create" — lead value (both or neither, e.g. 1000 /
                "EUR").
            expected_close_date: op="create" — YYYY-MM-DD.
            limit: op="list" — page size.
            start: op="list" — offset (v1 pagination).
        """
        if op == "list":
            return _client().list_leads(
                owner_id=owner_id, person_id=person_id,
                organization_id=organization_id, filter_id=filter_id,
                archived_status=archived_status, limit=limit, start=start)

        if op == "create":
            title = _need(title, "title", op)
            if not (person_id or organization_id):
                raise _bad("op='create' requiert person_id ou organization_id")
            value = ({"amount": amount, "currency": currency or "EUR"}
                     if amount is not None else None)
            return _client().create_lead(
                title, person_id=person_id, organization_id=organization_id,
                owner_id=owner_id, value=value,
                expected_close_date=expected_close_date)

        raise _bad("op doit être 'list' ou 'create'")

    # ---- utilisateurs du compte (API v1) ---------------------------------

    @mcp.tool()
    def pipedrive_users() -> dict:
        """List account users — to set `owner_id` when creating or assigning."""
        return _client().list_users()
