"""Webflow — CMS (site/collections/items) + webhooks, API v2
(developers.webflow.com/data).

Wrappe `oto.tools.webflow.client.WebflowClient`. Credential = clé unique
(`keyed=True`, `secret_kind="api_key"`, `access.resolve_api_key("webflow")`) :
un Site API token Webflow est bound à UN site (vérifié contre
`reference/authentication/site-token` — « Site tokens are created per site »),
donc AUCUN `site_id` à saisir ni à faire voyager ici — le client (oto-core)
le résout lui-même via `GET /sites` au premier appel qui en a besoin, mis en
cache pour la durée de vie du client. byo-only (pas de clé plateforme).

⚠️ **Un item créé/modifié ici reste invisible sur le site public tant que
`webflow_publish` n'a pas été appelé** — le seul tool de ce module qui touche
le contenu LIVE, laissé SEUL (comme `cognism_redeem` face à `cognism_search` :
la frontière entre « rien ne bouge » et « ça devient public » doit rester
visible dans le nom du tool, pas noyée dans un `op` parmi d'autres). Tout le
reste — site/collections/items — est UN SEUL tool consolidé, `webflow_cms`,
verbe en `op` (convention Folk/Cognism ADR 0047 §Amendement) : côté agent
comme côté catalogue dashboard (qui liste les outils PAR connecteur sous une
même carte), le CMS se présente comme une chose, pas quatre. create/update
valident `fieldData` contre le schéma réel de la collection
(`webflow_cms(op="collection")`) avant tout appel réseau d'écriture : un slug
inconnu ou un champ requis manquant est nommé dans l'erreur plutôt que de
laisser filer un 400 Webflow opaque à l'agent.

`webflow_webhooks` (list/get/create/delete) est la surface RÉELLE de l'API —
**aucun endpoint update n'existe** (vérifié live 2026-08-20 : reconfigurer un
webhook = delete + create). `secretKey` n'est renvoyé QU'À LA CRÉATION (jamais
sur get/list, confirmé live) — le docstring de `op="create"` le signale, comme
Folk pour `signingSecret`.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from oto.tools.common.errors import UpstreamHTTPError

from .. import access

_BULK_MAX_ITEMS = 50

_WebhookTriggerType = Literal[
    "form_submission", "site_publish",
    "page_created", "page_metadata_updated", "page_deleted",
    "ecomm_new_order", "ecomm_order_changed", "ecomm_inventory_changed",
    "collection_item_created", "collection_item_changed",
    "collection_item_deleted", "collection_item_published",
    "collection_item_unpublished", "comment_created",
]


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    if value is None:
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _run(fn):
    """Exécute un appel Webflow, traduit une erreur amont en McpError actionnable.

    `ValueError` = le client a résolu `site_id` via `GET /sites` et vu 0 ou
    >1 site (token de workspace passé par erreur, scope `sites:read` absent,
    token révoqué) — pas un refus HTTP, mais tout aussi actionnable pour
    l'appelant."""
    try:
        return fn()
    except McpError:
        raise
    except ValueError as e:
        raise _bad(str(e))
    except UpstreamHTTPError as e:
        if e.status_code == 401:
            msg = "Token Webflow invalide ou révoqué (401). Vérifie le token posé."
        elif e.status_code == 404:
            msg = f"Webflow : ressource introuvable (404) — {e.body}"
        elif e.status_code >= 500:
            msg = (f"Webflow est momentanément indisponible (erreur serveur "
                   f"{e.status_code}). Réessaie dans un moment — ce n'est pas "
                   "ton entrée.")
        else:
            msg = f"Webflow a refusé la requête (HTTP {e.status_code}) : {e.body}"
        raise _bad(msg)


def _known_field_slugs(collection: dict) -> set:
    return {"name", "slug"} | {
        f.get("slug") for f in collection.get("fields", []) if f.get("slug")
    }


def _required_field_slugs(collection: dict) -> set:
    return {"name", "slug"} | {
        f.get("slug") for f in collection.get("fields", [])
        if f.get("slug") and f.get("isRequired")
    }


def _validate_field_data(collection: dict, field_data: dict, *, op: str,
                          check_required: bool) -> None:
    """Refuse un `fieldData` AVANT tout appel réseau d'écriture : un slug
    inconnu ou (`check_required`) un champ requis absent nomme le(s) coupable(s)
    dans l'erreur, plutôt que de laisser Webflow renvoyer un 400 générique que
    l'agent ne peut pas exploiter."""
    known = _known_field_slugs(collection)
    unknown = set(field_data) - known
    if unknown:
        raise _bad(
            f"webflow_cms(op='{op}') : champ(s) inconnu(s) dans fieldData "
            f"pour cette collection : {sorted(unknown)}. Champs disponibles : "
            f"{sorted(known)}.")
    if check_required:
        missing = _required_field_slugs(collection) - set(field_data)
        if missing:
            raise _bad(
                f"webflow_cms(op='{op}') : champ(s) requis manquant(s) dans "
                f"fieldData : {sorted(missing)}.")


def register(mcp: FastMCP) -> None:
    from oto.tools.webflow.client import WebflowClient

    def _client() -> WebflowClient:
        key, _ = access.resolve_api_key("webflow")
        return WebflowClient(api_key=key)

    @mcp.tool()
    def webflow_cms(
        op: Literal["site", "collections", "collection", "items", "item",
                    "create", "update", "delete"],
        collection_id: Optional[str] = None,
        id: Optional[str] = None,
        ids: Optional[list[str]] = None,
        item: Optional[dict] = None,
        items: Optional[list[dict]] = None,
        offset: int = 0,
        max_results: int = 100,
        sort_by: Optional[Literal["createdOn", "lastPublished", "lastUpdated",
                                   "name", "slug"]] = None,
        sort_order: Optional[Literal["asc", "desc"]] = None,
        cms_locale_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Webflow CMS — the site, its collections, and their items. This is
        THE tool for "what's my Webflow site", "list my CMS collections", and
        "find/add/update/delete an item" (a blog post, a product, any CMS
        record) — Webflow has no separate `webflow_site`/`webflow_collections`
        tool, `op` picks the target+verb. Items created/updated here are
        STAGED (draft): nothing is visible on the live site until
        `webflow_publish`.

        `op`:
        - **"site"**: the site pinned to this connector's credential (a
          Webflow Site API token is bound to exactly one site — no `site_id`
          param, this always returns THAT site: id, displayName, shortName,
          customDomains, lastPublished, timeZone...).
        - **"collections"**: list all CMS collections of the site (id,
          displayName, slug — no field schema, use op="collection" for that).
        - **"collection"**: one collection's full schema (`collection_id`),
          including `fields[]` (each field's `slug`, `displayName`, `type`,
          `isRequired`) — the slugs op="create"/"update" need for `fieldData`.
        - **"items"**: one paginated page of a collection's items
          (`collection_id` + `offset`/`max_results`, capped at 500),
          optionally sorted.
        - **"item"**: one item by `id` (+ `collection_id`).
        - **"create"**: one (`item`) or several (`items`, ≤50) new items in
          `collection_id`. `fieldData` keys are validated against the
          collection's schema (op="collection") before any write — an unknown
          slug or a missing required field is refused first.
        - **"update"**: PATCH one (`id` + `item`) or several (`items`, ≤50 —
          each needs an `"id"` key) items in `collection_id`.
        - **"delete"**: delete one (`id`) or several (`ids`, ≤50) items from
          `collection_id`.

        Solo vs bulk: exactly one of `item`/`items` (create), `id`/`items`
        (update), `id`/`ids` (delete) is required. Webflow has a REAL batch
        endpoint (all items in one HTTP call) — bulk here is one request, not
        a client-side loop.

        Args:
            op: site | collections | collection | items | item | create |
                update | delete.
            collection_id: required for every op except "site".
            id: item id — op="item", solo update/delete.
            ids: item ids — bulk delete.
            item: op="create" solo — `{"fieldData": {...}, "isArchived"?: bool,
                "isDraft"?: bool}`. op="update" solo (with `id` set separately)
                — same shape, `fieldData` optional (only given keys change).
            items: op="create" bulk — list of the create shape above.
                op="update" bulk — list of `{"id", "fieldData"?, "isArchived"?,
                "isDraft"?}`.
            offset, max_results: op="items" pagination — max_results capped at
                500 server-side.
            sort_by, sort_order: op="items" — sort the page.
            cms_locale_id: op="items"/"item" — locale for multi-locale
                collections.
            dry_run: create — validates `fieldData` against the schema (one
                read call), makes NO create call, returns `would_create`.
                update/delete — fetches the item(s) first and returns a real
                diff (`changes: {field: {"from", "to"}}`) or `would_delete`
                (the current record) — never an echo of the input.

        Returns:
            site: the site.
            collections: `{"collections": [...]}`.
            collection: the collection schema.
            items: `{"items": [...], "pagination": {"total", "offset", "limit"}}`.
            item: the item.
            create solo: the created item, or `{"dry_run": true, "would_create"}`.
            create bulk: `{"total", "succeeded", "created": [{"index","id"}],
                "failed": []}`, or dry_run preview.
            update solo: the updated item, or `{"dry_run": true, "id", "changes"}`.
            update bulk: `{"total", "succeeded", "failed": []}`, or dry_run preview.
            delete solo: `{}`, or `{"dry_run": true, "id", "would_delete"}`.
            delete bulk: `{"total", "succeeded", "failed": []}`, or dry_run preview.
        """
        c = _client()

        if op == "site":
            return _run(lambda: c.get_site())

        if op == "collections":
            return {"collections": _run(lambda: c.list_collections())}

        if op == "collection":
            _need(collection_id, "collection_id", op)
            return _run(lambda: c.get_collection(collection_id))

        _need(collection_id, "collection_id", op)

        if op == "items":
            return _run(lambda: c.list_items(
                collection_id, offset=offset, limit=min(max_results, 500),
                sort_by=sort_by, sort_order=sort_order,
                cms_locale_id=cms_locale_id))

        if op == "item":
            _need(id, "id", op)
            return _run(lambda: c.get_item(collection_id, id))

        if op == "create":
            if (item is None) == (items is None):
                raise _bad("op='create' : fournir soit `item` soit `items` — "
                           "pas les deux, pas ni l'un ni l'autre.")
            payload = [item] if item is not None else list(items)
            if len(payload) > _BULK_MAX_ITEMS:
                raise _bad(f"trop d'éléments ({len(payload)}) — max "
                           f"{_BULK_MAX_ITEMS} par appel.")
            collection = _run(lambda: c.get_collection(collection_id))
            for it in payload:
                _validate_field_data(collection, it.get("fieldData") or {},
                                     op=op, check_required=True)
            if dry_run:
                if item is not None:
                    return {"dry_run": True, "would_create": payload[0]}
                return {"dry_run": True, "total": len(payload),
                        "would_create": payload}
            result = _run(lambda: c.create_items(collection_id, payload))
            created = result.get("items", [])
            if item is not None:
                return created[0] if created else {}
            return {"total": len(payload), "succeeded": len(created),
                    "created": [{"index": i, "id": it.get("id")}
                               for i, it in enumerate(created)],
                    "failed": []}

        if op == "update":
            if (id is None) == (items is None):
                raise _bad("op='update' : fournir soit `id` (+ `item`) pour UN "
                           "item, soit `items` pour plusieurs — pas les deux, "
                           "pas ni l'un ni l'autre.")

            def _diff(current: dict, changed: dict) -> dict:
                changes = {}
                for k, v in changed.items():
                    if k in ("id",):
                        continue
                    if k == "fieldData":
                        for fk, fv in (v or {}).items():
                            changes[fk] = {
                                "from": (current.get("fieldData") or {}).get(fk),
                                "to": fv}
                    else:
                        changes[k] = {"from": current.get(k), "to": v}
                return changes

            if id is not None:
                field_data = (item or {}).get("fieldData")
                if field_data:
                    collection = _run(lambda: c.get_collection(collection_id))
                    _validate_field_data(collection, field_data, op=op,
                                         check_required=False)
                if dry_run:
                    current = _run(lambda: c.get_item(collection_id, id))
                    return {"dry_run": True, "id": id,
                            "changes": _diff(current, item or {})}
                payload = {"id": id, **(item or {})}
                result = _run(lambda: c.update_items(collection_id, [payload]))
                updated = result.get("items", [])
                return updated[0] if updated else {}

            if len(items) > _BULK_MAX_ITEMS:
                raise _bad(f"trop d'éléments ({len(items)}) — max "
                           f"{_BULK_MAX_ITEMS} par appel.")
            for it in items:
                if "id" not in it:
                    raise _bad("chaque item doit contenir 'id'.")
            needs_schema = any(it.get("fieldData") for it in items)
            if needs_schema:
                collection = _run(lambda: c.get_collection(collection_id))
                for it in items:
                    if it.get("fieldData"):
                        _validate_field_data(collection, it["fieldData"], op=op,
                                             check_required=False)
            if dry_run:
                would_update = []
                for it in items:
                    current = _run(lambda it=it: c.get_item(collection_id, it["id"]))
                    would_update.append({"id": it["id"], "changes": _diff(current, it)})
                return {"dry_run": True, "total": len(items),
                        "would_update": would_update}
            result = _run(lambda: c.update_items(collection_id, items))
            updated = result.get("items", [])
            return {"total": len(items), "succeeded": len(updated), "failed": []}

        if op == "delete":
            if (id is None) == (ids is None):
                raise _bad("op='delete' : fournir soit `id` soit `ids` — pas "
                           "les deux, pas ni l'un ni l'autre.")
            target_ids = [id] if id is not None else list(ids)
            if len(target_ids) > _BULK_MAX_ITEMS:
                raise _bad(f"trop d'éléments ({len(target_ids)}) — max "
                           f"{_BULK_MAX_ITEMS} par appel.")
            if dry_run:
                would_delete = [_run(lambda tid=tid: c.get_item(collection_id, tid))
                                for tid in target_ids]
                if id is not None:
                    return {"dry_run": True, "id": id,
                            "would_delete": would_delete[0]}
                return {"dry_run": True, "total": len(target_ids),
                        "would_delete": would_delete}
            _run(lambda: c.delete_items(collection_id, target_ids))
            if id is not None:
                return {}
            return {"total": len(target_ids), "succeeded": len(target_ids),
                    "failed": []}

        raise _bad("op doit être 'site', 'collections', 'collection', "
                   "'items', 'item', 'create', 'update' ou 'delete'.")

    @mcp.tool()
    def webflow_publish(
        collection_id: str,
        id: Optional[str] = None,
        ids: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> dict:
        """Publish staged (draft) CMS items to the LIVE site — the only tool in
        this connector that makes content publicly visible (Webflow).

        Args:
            collection_id: the collection (see `webflow_cms(op="collections")`).
            id: one item id, OR...
            ids: several item ids (≤50).
            dry_run: resolves the item(s) and returns their CURRENT
                `isDraft`/`lastPublished` state (what would go live) — not an
                echo of the ids you passed. No publish call is made.
        """
        if (id is None) == (ids is None):
            raise _bad("fournir soit `id` soit `ids` — pas les deux, pas ni "
                       "l'un ni l'autre.")
        target_ids = [id] if id is not None else list(ids)
        if len(target_ids) > _BULK_MAX_ITEMS:
            raise _bad(f"trop d'éléments ({len(target_ids)}) — max "
                       f"{_BULK_MAX_ITEMS} par appel.")
        c = _client()
        if dry_run:
            would_publish = []
            for tid in target_ids:
                current = _run(lambda tid=tid: c.get_item(collection_id, tid))
                would_publish.append({
                    "id": tid, "isDraft": current.get("isDraft"),
                    "lastPublished": current.get("lastPublished"),
                })
            if id is not None:
                return {"dry_run": True, "would_publish": would_publish[0]}
            return {"dry_run": True, "total": len(target_ids),
                    "would_publish": would_publish}
        return _run(lambda: c.publish_items(collection_id, target_ids))

    @mcp.tool()
    def webflow_webhooks(
        op: Literal["list", "get", "create", "delete"] = "list",
        webhook_id: Optional[str] = None,
        trigger_type: Optional[_WebhookTriggerType] = None,
        url: Optional[str] = None,
        filter: Optional[dict] = None,
        dry_run: bool = False,
    ) -> dict:
        """Webflow webhooks on the pinned site — list, inspect, create, delete.
        Webflow POSTs an event payload to `url` each time `trigger_type` fires
        (site publish, a CMS item created/changed/deleted/published, a form
        submission, an ecommerce event...).

        ⚠️ **No update endpoint exists** (confirmed against Webflow's actual
        API, not just the docs) — to change a webhook's trigger/url/filter,
        delete it and create a new one.

        `op`:
        - **"list"** (default): every webhook registered on this site.
        - **"get"**: one webhook by `webhook_id`.
        - **"create"**: register a new webhook (`trigger_type` + `url`,
          optional `filter`). The response's `secretKey` (HMAC signing key for
          `x-webflow-signature`) is returned IN FULL only here — Webflow never
          shows it again on get/list, save it now if you need to verify
          payload signatures.
        - **"delete"**: unregister `webhook_id`. Irreversible — the webhook
          stops firing immediately.

        Args:
            op: list (default) | get | create | delete.
            webhook_id: op="get"/"delete".
            trigger_type: op="create" — the event that fires this webhook.
            url: op="create" — the HTTPS endpoint Webflow POSTs the event to.
            filter: op="create" — ONLY valid for trigger_type="form_submission"
                (`{"name": "<form name>"}`, scope to one form). Any other
                trigger_type + a filter is refused before the network call
                (Webflow itself 400s on this combination).
            dry_run: create — makes NO create call, returns `would_create`
                (no secretKey to preview — it doesn't exist until Webflow
                mints it). delete — fetches the webhook first and returns
                `would_delete` (its current record), never a bare echo of
                `webhook_id`.

        Returns:
            list: `{"webhooks": [...]}`.
            get: the webhook.
            create: the webhook INCLUDING `secretKey`, or
                `{"dry_run": true, "would_create"}`.
            delete: `{}`, or `{"dry_run": true, "webhook_id", "would_delete"}`.
        """
        c = _client()

        if op == "list":
            return {"webhooks": _run(lambda: c.list_webhooks())}

        if op == "get":
            _need(webhook_id, "webhook_id", op)
            return _run(lambda: c.get_webhook(webhook_id))

        if op == "create":
            _need(trigger_type, "trigger_type", op)
            _need(url, "url", op)
            if filter is not None and trigger_type != "form_submission":
                raise _bad(
                    "op='create' : `filter` n'est valide que pour "
                    "trigger_type='form_submission' — Webflow refuse toute "
                    f"autre combinaison (reçu trigger_type={trigger_type!r}).")
            if dry_run:
                preview = {"triggerType": trigger_type, "url": url}
                if filter is not None:
                    preview["filter"] = filter
                return {"dry_run": True, "would_create": preview}
            return _run(lambda: c.create_webhook(trigger_type, url, filter=filter))

        if op == "delete":
            _need(webhook_id, "webhook_id", op)
            if dry_run:
                current = _run(lambda: c.get_webhook(webhook_id))
                return {"dry_run": True, "webhook_id": webhook_id,
                        "would_delete": current}
            _run(lambda: c.delete_webhook(webhook_id))
            return {}

        raise _bad("op doit être 'list', 'get', 'create' ou 'delete'.")
