"""Brevo — native CRM (deals, companies, tasks, notes, pipelines), v3 API.

Second module of the `brevo` connector (see `Connector.modules` in the registry): same
key, same client, distinct subdomain. Tools remain prefixed `brevo_` — the
activation gate's namespace is the 1st token (`brevo`).

Generic surface (`entity` as a parameter) rather than 4×4 tools: the four objects
share list/get/create/update. The API's asymmetries (`/companies` path outside
`/crm`, pagination by page, `filters[]`/`filter[]` prefix) are absorbed by
`oto.tools.brevo.CrmMixin`, not here.

Deletions not exposed (consistent with `tools/brevo.py`).
"""
from __future__ import annotations

from typing import Any, Optional

from fastmcp import FastMCP

from .. import access


def register(mcp: FastMCP) -> None:
    from oto.tools.brevo import BrevoClient

    def _client() -> BrevoClient:
        key, _ = access.resolve_api_key("brevo")
        return BrevoClient(api_key=key)


    @mcp.tool()
    def brevo_crm_list(
        entity: str,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[dict] = None,
        sort_by: Optional[str] = None,
    ) -> dict:
        """List Brevo CRM objects.

        Args:
            entity: `deals` | `companies` | `tasks` | `notes`.
            filters: RAW keys of the entity —
                deals: `{"attributes.deal_name": "Acme", "linkedContactsIds": "12"}`;
                companies: `{"attributes.name": "Acme"}`;
                tasks: `{"status": "done", "type": …, "contacts": "12"}`;
                notes: `{"entity": "deals", "entityIds": "<id>"}`.
        """
        return _client().crm_list(
            entity, limit=limit, offset=offset, filters=filters, sort_by=sort_by)

    @mcp.tool()
    def brevo_crm_get(entity: str, object_id: str) -> dict:
        """Fetch a Brevo CRM object by id (`deals`|`companies`|`tasks`|`notes`)."""
        return _client().crm_get(entity, object_id)

    @mcp.tool()
    def brevo_crm_create(entity: str, payload: dict) -> dict:
        """Create a Brevo CRM object. Returns `{"id": …}`.

        `payload` in Brevo camelCase. Required fields:
        - **deals**: `name` (+ `attributes`: `deal_stage`, `amount`, `close_date`…)
        - **companies**: `name` (+ `attributes`, `linkedContactsIds`)
        - **tasks**: `name`, `taskTypeId` (see `brevo_crm_meta`), `date` (ISO 8601)
        - **notes**: `text` (+ `contactIds`, `dealIds`, `companyIds`)

        Custom `attributes` are read via `brevo_crm_meta`.
        """
        return _client().crm_create(entity, payload)

    @mcp.tool()
    def brevo_crm_update(entity: str, object_id: str, payload: dict) -> dict:
        """Update a Brevo CRM object (provided fields only).

        To attach/detach linked objects, use `brevo_crm_link`.
        """
        return _client().crm_update(entity, object_id, payload)

    @mcp.tool()
    def brevo_crm_link(
        entity: str,
        object_id: str,
        link_contact_ids: Optional[list[int]] = None,
        unlink_contact_ids: Optional[list[int]] = None,
        link_ids: Optional[list[str]] = None,
        unlink_ids: Optional[list[str]] = None,
    ) -> dict:
        """Attach/detach linked objects — `deals` and `companies` only.

        `link_ids`/`unlink_ids` target the complementary object: the **companies**
        of a deal, the **deals** of a company.
        """
        return _client().crm_link(
            entity, object_id, link_contact_ids=link_contact_ids,
            unlink_contact_ids=unlink_contact_ids, link_ids=link_ids,
            unlink_ids=unlink_ids)

    @mcp.tool()
    def brevo_crm_meta(entity: Optional[str] = None) -> dict:
        """Brevo CRM metadata: pipelines + stages, task types, attributes.

        Read before `brevo_crm_create`: a `deal_stage` is designated by the pipeline's
        stage `id`, a `taskTypeId` by the id of its type.

        Args:
            entity: `deals` | `companies` → attaches their custom attributes.
        """
        client = _client()
        out: dict[str, Any] = {
            "pipelines": client.pipelines(),
            "task_types": client.task_types(),
        }
        if entity:
            out["attributes"] = client.crm_attributes(entity)
        return out
