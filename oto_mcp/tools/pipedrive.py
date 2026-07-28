"""Pipedrive CRM — deals, personnes, organisations, activités, notes, leads.

Wrappe `oto.tools.pipedrive.PipedriveClient`. Credential = **token API personnel**
(`api_token`) + `company_domain` facultatif (non secret, route vers le data center
du compte) → modèle générique multi-champs (ADR 0011), résolu par appel via
`access.resolve_credential_fields("pipedrive")`. byo_user OU byo_org, pas de clé
plateforme (le token EST le grant).

Surface **générique par `entity`** (comme hubspot/salesforce) plutôt qu'un tool par
objet : deals/persons/organizations/activities/products/pipelines/stages partagent
les mêmes verbes en API v2. Ce que Pipedrive n'a pas porté en v2 garde ses tools
dédiés (notes, leads, users) — la frontière est dans l'API, autant l'assumer.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access, connector_verify


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

    @mcp.tool()
    def pipedrive_search(
        entity: str,
        term: str,
        fields: Optional[str] = None,
        exact_match: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
        person_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> dict:
        """Full-text search within one entity.

        Args:
            entity: deals | persons | organizations | products | leads.
            term: at least 2 characters (1 if exact_match).
            fields: comma-separated fields to search in (e.g. "name,email" on
                persons; defaults to every searchable field).
            exact_match: case-insensitive exact match instead of partial.
            cursor: `next_cursor` from a previous response.
            person_id/organization_id: restrict deals to a linked record.
            status: deals only — open | won | lost.
        """
        extra = {}
        if person_id is not None:
            extra["person_id"] = person_id
        if organization_id is not None:
            extra["organization_id"] = organization_id
        if status:
            extra["status"] = status
        return _client().search(
            entity, term, fields=fields, exact_match=exact_match, limit=limit,
            cursor=cursor, **extra)

    @mcp.tool()
    def pipedrive_search_all(
        term: str,
        item_types: Optional[str] = None,
        fields: Optional[str] = None,
        exact_match: bool = False,
        search_for_related_items: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """Search across several object types at once.

        Args:
            item_types: comma-separated — deal, person, organization, product,
                lead, file, mail_attachment, project (defaults to all).
            search_for_related_items: also return records linked to the matches.
        """
        return _client().search_all(
            term, item_types=item_types, fields=fields, exact_match=exact_match,
            search_for_related_items=search_for_related_items, limit=limit,
            cursor=cursor)

    @mcp.tool()
    def pipedrive_list(
        entity: str,
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
        """List records of an entity (cursor-paginated).

        Args:
            entity: deals | persons | organizations | activities | products |
                pipelines | stages.
            limit: up to 500.
            cursor: `next_cursor` from a previous response (null = last page).
            filter_id: id of a saved Pipedrive filter.
            updated_since: RFC 3339 timestamp — only records changed since.
            sort_by: id | update_time | add_time (entity-dependent).
            custom_fields: comma-separated custom field keys to include (their
                40-char hashes come from `pipedrive_fields`).
        """
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

    @mcp.tool()
    def pipedrive_get(
        entity: str,
        record_id: int,
        include_fields: Optional[str] = None,
        custom_fields: Optional[str] = None,
    ) -> dict:
        """Fetch one record by id (entity = deals | persons | organizations | …)."""
        return _client().get_record(
            entity, record_id, include_fields=include_fields,
            custom_fields=custom_fields)

    @mcp.tool()
    def pipedrive_create(entity: str, data: dict) -> dict:
        """Create a record.

        Args:
            data: API v2 body — e.g. {"title": …, "value": 1000, "currency":
                "EUR", "person_id": …} for a deal; {"name": …, "emails":
                [{"value": "a@b.c", "primary": true}]} for a person. Custom
                fields go under {"custom_fields": {"<hash>": value}}.
        """
        return _client().create_record(entity, data)

    @mcp.tool()
    def pipedrive_update(entity: str, record_id: int, data: dict) -> dict:
        """Update a record (partial — only the keys you pass are changed)."""
        return _client().update_record(entity, record_id, data)

    @mcp.tool()
    def pipedrive_delete(entity: str, record_id: int) -> dict:
        """Delete a record."""
        return _client().delete_record(entity, record_id)

    @mcp.tool()
    def pipedrive_fields(entity: str, limit: int = 100,
                         cursor: Optional[str] = None) -> dict:
        """List an entity's fields — the only way to get custom field keys.

        Custom fields are keyed by a 40-char hash, both in responses and when
        writing. Call this first when a deal/person carries client-specific data.

        Args:
            entity: deals | persons | organizations | products | activities.
        """
        return _client().list_fields(entity, limit=limit, cursor=cursor)

    @mcp.tool()
    def pipedrive_notes(
        deal_id: Optional[int] = None,
        person_id: Optional[int] = None,
        org_id: Optional[int] = None,
        lead_id: Optional[str] = None,
        limit: int = 100,
        start: int = 0,
    ) -> dict:
        """List notes attached to a deal / person / organization / lead.

        Offset-paginated (`start`): notes are still on Pipedrive's v1 API.
        """
        return _client().list_notes(
            deal_id=deal_id, person_id=person_id, org_id=org_id, lead_id=lead_id,
            limit=limit, start=start)

    @mcp.tool()
    def pipedrive_create_note(
        content: str,
        deal_id: Optional[int] = None,
        person_id: Optional[int] = None,
        org_id: Optional[int] = None,
        lead_id: Optional[str] = None,
    ) -> dict:
        """Attach a note (HTML accepted) to a deal / person / organization / lead.

        Exactly one target id is expected — a note with no target is rejected.
        """
        return _client().create_note(
            content, deal_id=deal_id, person_id=person_id, org_id=org_id,
            lead_id=lead_id)

    @mcp.tool()
    def pipedrive_leads(
        owner_id: Optional[int] = None,
        person_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        filter_id: Optional[int] = None,
        archived_status: Optional[str] = None,
        limit: int = 100,
        start: int = 0,
    ) -> dict:
        """List leads (Pipedrive's pre-deal inbox).

        Args:
            archived_status: archived | not_archived | all.
        """
        return _client().list_leads(
            owner_id=owner_id, person_id=person_id,
            organization_id=organization_id, filter_id=filter_id,
            archived_status=archived_status, limit=limit, start=start)

    @mcp.tool()
    def pipedrive_create_lead(
        title: str,
        person_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        owner_id: Optional[int] = None,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        expected_close_date: Optional[str] = None,
    ) -> dict:
        """Create a lead — requires person_id or organization_id.

        Args:
            amount/currency: lead value (both or neither, e.g. 1000 / "EUR").
            expected_close_date: YYYY-MM-DD.
        """
        value = {"amount": amount, "currency": currency or "EUR"} if amount is not None else None
        return _client().create_lead(
            title, person_id=person_id, organization_id=organization_id,
            owner_id=owner_id, value=value,
            expected_close_date=expected_close_date)

    @mcp.tool()
    def pipedrive_users() -> dict:
        """List account users — to set `owner_id` when creating or assigning."""
        return _client().list_users()
