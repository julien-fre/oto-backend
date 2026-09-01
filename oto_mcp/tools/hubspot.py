"""HubSpot CRM — contacts, companies, deals, tickets, notes (read + write).

Wrappe `oto.tools.hubspot.HubSpotClient` (private app token). Clé résolue par
appel via `access.resolve_api_key("hubspot")` — byo (user key sur /account ou
credential partagé de l'org). Pas de clé plateforme.

Surface générique : `object_type` = contacts | companies | deals | tickets
(ou tout objet custom) pour search/get/create/update/delete — fusion sans perte.

**Surface consolidée (ADR 0047 §Amendement)** : 9 tools → 2. Les HUIT verbes qui
portaient `object_type` (`search`/`get`/`list`/`create`/`update`/`delete`/
`associations`/`create_note`) vivent dans **`hubspot_object`**, le verbe en `op` —
ils partageaient déjà leurs paramètres (`object_type`, `object_id`, `properties`),
et c'est ÇA le critère de fusion, pas le comptage. **`hubspot_owners` reste SEUL** :
il ne prend aucun paramètre d'objet CRM (ni `object_type`, ni `object_id`, ni
`properties`) et lit un référentiel d'utilisateurs, pas un enregistrement — le
fusionner n'aurait factorisé aucun paramètre, donc pesé autant que deux tools.

⚠️ Deux paramètres sont des HOMONYMES dont le type dépend de l'`op` — c'est le prix
de la fusion, et il est payé par une validation DURE (jamais une coercition ni un
fallback silencieux : la mauvaise forme lève ici plutôt que de partir chez HubSpot
qui répondrait un 400 opaque) :
- `properties` = list[str] (noms de propriétés à RETOURNER) en lecture
  (search/list/get) ; dict {propriété: valeur} à ÉCRIRE en écriture
  (create/update).
- `associations` = list[str] (types d'objets dont on veut les ids liés) sur
  op="get" ; list[dict] (objets d'association HubSpot v3) sur op="create".

**`hubspot_list` — les « segments »**. Les listes HubSpot SONT le mécanisme de
segmentation (la doc les décrit comme servant au « record segmentation »), il n'y
a pas d'API `segments` séparée. Deux pièges structurels, traités ici et pas chez
l'agent :
1. Les listes sont keyées sur un `objectTypeId` NUMÉRIQUE (`0-1` contacts, `0-2`
   companies, `0-3` deals, `0-5` tickets, `2-<n>` custom) là où tout le reste du
   connecteur parle en `"contacts"`. On accepte le nom ET l'id brut, on traduit.
2. Une liste `DYNAMIC` REFUSE les écritures d'appartenance (ses membres sont
   recalculés depuis ses critères). On lit son `processingType` AVANT d'écrire
   pour rendre un message actionnable, plutôt que de laisser partir un 400 opaque.

**`filterBranch` est un passe-plat assumé** : l'arbre de critères HubSpot est
récursif (`filterBranchType` OR/AND/UNIFIED_EVENTS/ASSOCIATION, forme d'`operation`
par `filterType`) — le modéliser coûterait une page de schéma pour peu de gain. On
le transmet tel quel, en dict, documenté comme avancé. C'est `hubspot_property` qui
rend ce passe-plat utilisable : un `filterBranch` référence des propriétés par NOM
INTERNE (`dealstage`, pas « Deal stage ») et les listes déroulantes n'acceptent que
leurs `options[].value` — sans ce référentiel, tout critère (et tout create/update)
est une devinette.

⚠️ **Scopes** : les listes exigent `crm.lists.read` / `crm.lists.write` dans la
private app. Les tokens créés avant ces tools n'ont que les scopes `crm.objects.*`
→ un 403 ici veut dire « ajoute le scope », PAS « clé invalide ».
"""
from __future__ import annotations

import re
from typing import Literal, Optional, Union

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access


def register(mcp: FastMCP) -> None:
    from oto.tools.hubspot.client import HubSpotClient

    def _client() -> HubSpotClient:
        key, _ = access.resolve_api_key("hubspot")
        return HubSpotClient(api_key=key)

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _need(value, name: str, op: str):
        """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback."""
        if value is None:
            raise _bad(f"op='{op}' requiert {name}")
        return value

    def _names(value, name: str, op: str) -> Optional[list]:
        """Forme LECTURE d'un paramètre homonyme : une liste de NOMS (list[str]).

        `properties` et `associations` changent de type selon l'op (cf. docstring
        du module) : on refuse ici la forme d'écriture au lieu de la transmettre.
        """
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise _bad(
                f"op='{op}' attend {name} = liste de noms de propriétés (list[str]) ; "
                "la forme dict/objets est celle des op d'écriture")
        return value

    def _payload(value, name: str, op: str) -> dict:
        """Forme ÉCRITURE de `properties` : un dict {propriété: valeur}."""
        _need(value, name, op)
        if not isinstance(value, dict):
            raise _bad(
                f"op='{op}' attend {name} = dict {{propriété: valeur}} ; la liste de "
                "noms est la forme des op de lecture")
        return value

    def _assoc_objects(value, op: str) -> Optional[list]:
        """Forme ÉCRITURE d'`associations` : objets d'association HubSpot v3."""
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise _bad(
                f"op='{op}' attend associations = liste d'objets d'association "
                "HubSpot v3 (list[dict]) ; la liste de types est la forme d'op='get'")
        return value

    @mcp.tool()
    def hubspot_object(
        op: Literal["search", "list", "get", "create", "update", "delete",
                    "associations", "add_note"] = "search",
        object_type: Optional[str] = None,
        object_id: Optional[str] = None,
        properties: Optional[Union[list[str], dict]] = None,
        associations: Optional[Union[list[str], list[dict]]] = None,
        query: Optional[str] = None,
        filters: Optional[list[dict]] = None,
        to_object_type: Optional[str] = None,
        body: Optional[str] = None,
        limit: int = 100,
        after: Optional[str] = None,
    ) -> dict:
        """HubSpot CRM objects — one tool, the verb in `op`.

        `object_type` = contacts | companies | deals | tickets (or any custom
        object) drives every op, and is always required.

        Ops:
        - **"search"** (default) : Search CRM objects. Full-text via `query`,
          structured via `filters`. Paginated (`limit`, `after`).
        - **"list"** : List CRM objects of a type (paginated via `after`).
        - **"get"** : Fetch one CRM object by id. Requires `object_id`.
        - **"create"** : Create a CRM object. Requires `properties` (dict).
        - **"update"** : Update (PATCH) a CRM object's properties. Requires
          `object_id` + `properties` (dict).
        - **"delete"** : Archive a CRM object (moves it to HubSpot's recycle bin).
          Requires `object_id`.
        - **"associations"** : List objects of `to_object_type` associated with an
          object. e.g. the deals of a contact: object_type="contacts",
          to_object_type="deals". Requires `object_id` + `to_object_type`.
        - **"add_note"** : Attach a note to a CRM object (contacts/companies/deals/
          tickets). Requires `body` + `object_id` (the object the note hangs on).

        ⚠️ `properties` and `associations` are HOMONYMS whose expected type depends
        on `op` (read = list of names, write = dict / association objects) — see the
        Args below. A wrong shape is refused with an explicit error, never coerced.

        Args:
            op: search | list | get | create | update | delete | associations |
                add_note.
            object_type: contacts | companies | deals | tickets (or custom).
                Required for every op ; on op="add_note" it is the type of the
                object the note is attached to.
            object_id: id of the object — required for get, update, delete,
                associations and add_note.
            properties:
                - READ (search, list, get) : property names to return (list[str]).
                - WRITE (create, update) : object properties (dict), e.g.
                  {"email": …, "firstname": …} for a contact ; {"dealname": …,
                  "amount": …} for a deal.
            associations:
                - op="get" : other object types to return associated ids for
                  (e.g. ["companies", "deals"] on a contact).
                - op="create" : HubSpot v3 association objects (advanced).
            query: op="search" — full-text search.
            filters: op="search" — list of {propertyName, operator, value} combined
                with AND. operators: EQ, NEQ, GT, GTE, LT, LTE, CONTAINS_TOKEN,
                HAS_PROPERTY, IN (then pass "values": [...] instead of "value").
            to_object_type: op="associations" — the associated object type to list.
            body: op="add_note" — the note content (text/HTML).
            limit: op="search"/"list" — page size (HubSpot caps it at 100).
            after: op="search"/"list" — pagination cursor from a previous response
                (paging.next.after).
        """
        c = _client()

        if op == "search":
            return c.search_objects(
                _need(object_type, "object_type", op),
                query=query, filters=filters,
                properties=_names(properties, "properties", op),
                limit=limit, after=after)

        if op == "list":
            return c.list_objects(
                _need(object_type, "object_type", op),
                properties=_names(properties, "properties", op),
                limit=limit, after=after)

        if op == "get":
            return c.get_object(
                _need(object_type, "object_type", op),
                _need(object_id, "object_id", op),
                properties=_names(properties, "properties", op),
                associations=_names(associations, "associations", op))

        if op == "create":
            return c.create_object(
                _need(object_type, "object_type", op),
                _payload(properties, "properties", op),
                associations=_assoc_objects(associations, op))

        if op == "update":
            return c.update_object(
                _need(object_type, "object_type", op),
                _need(object_id, "object_id", op),
                _payload(properties, "properties", op))

        if op == "delete":
            return c.delete_object(
                _need(object_type, "object_type", op),
                _need(object_id, "object_id", op))

        if op == "associations":
            return c.list_associations(
                _need(object_type, "object_type", op),
                _need(object_id, "object_id", op),
                _need(to_object_type, "to_object_type", op))

        if op == "add_note":
            return c.create_note(
                _need(body, "body", op),
                _need(object_type, "object_type", op),
                _need(object_id, "object_id", op))

        raise _bad("op doit être 'search', 'list', 'get', 'create', 'update', "
                   "'delete', 'associations' ou 'add_note'")

    # objectTypeId : les listes sont keyées sur l'id numérique, pas sur le nom
    # d'objet. On accepte les deux — le nom pour les quatre standard, l'id brut
    # `N-N` pour tout le reste (aucune table ne peut couvrir les objets custom,
    # dont l'id dépend du portail).
    _OBJECT_TYPE_IDS = {
        "contacts": "0-1", "companies": "0-2", "deals": "0-3", "tickets": "0-5",
    }

    def _object_type_id(value, op: str) -> str:
        """Traduit `object_type` en `objectTypeId` HubSpot pour les listes."""
        _need(value, "object_type", op)
        key = str(value).strip().lower()
        if key in _OBJECT_TYPE_IDS:
            return _OBJECT_TYPE_IDS[key]
        if re.fullmatch(r"\d+-\d+", key):
            return key  # id brut (objet custom : `2-<n>`)
        raise _bad(
            f"object_type='{value}' inconnu pour les listes : attendu "
            "contacts | companies | deals | tickets, ou l'objectTypeId brut "
            "d'un objet custom (forme '2-7', lisible dans les réglages HubSpot)")

    def _ids(value, name: str, op: str) -> list:
        """Liste d'ids d'enregistrements — HubSpot les veut en chaînes."""
        _need(value, name, op)
        if not isinstance(value, list) or not value:
            raise _bad(f"op='{op}' attend {name} = liste non vide d'ids")
        return [str(v) for v in value]

    def _writable_list(c, list_id: str, op: str) -> dict:
        """Charge la liste et REFUSE d'écrire ses membres si elle est DYNAMIC.

        Une liste dynamique recalcule ses membres depuis ses critères ; HubSpot
        répond un 400 générique sur les endpoints d'appartenance. Un GET
        préalable coûte peu et permet de dire quoi faire à la place — et sert
        aussi d'état « avant » pour les dry_run.
        """
        current = c.get_list(list_id)
        info = current.get("list") or current
        if info.get("processingType") == "DYNAMIC":
            raise _bad(
                f"op='{op}' impossible : la liste {list_id} "
                f"(« {info.get('name')} ») est DYNAMIC — ses membres sont "
                "recalculés par HubSpot. Change ses critères "
                "(op='update' avec filter_branch), pas ses membres.")
        return info

    @mcp.tool()
    def hubspot_list(
        op: Literal["search", "get", "create", "update", "delete", "restore",
                    "members", "add_members", "remove_members", "clear_members",
                    "copy_from", "record_lists"] = "search",
        list_id: Optional[str] = None,
        object_type: Optional[str] = None,
        name: Optional[str] = None,
        processing_type: Literal["MANUAL", "DYNAMIC", "SNAPSHOT"] = "MANUAL",
        filter_branch: Optional[dict] = None,
        record_ids: Optional[list] = None,
        remove_record_ids: Optional[list] = None,
        record_id: Optional[str] = None,
        source_list_id: Optional[str] = None,
        query: Optional[str] = None,
        include_filters: bool = False,
        limit: int = 100,
        after: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """HubSpot lists — the segments of a HubSpot portal. One tool, verb in `op`.

        A HubSpot "segment" IS a list: there is no separate segments API. Three
        kinds, set at creation and NOT changeable afterwards:
        - **MANUAL** : you decide who is in it (the `*_members` ops below).
        - **DYNAMIC** : HubSpot recomputes membership from `filter_branch`. Its
          membership ops are REFUSED — change the criteria instead.
        - **SNAPSHOT** : filtered once at creation, then managed by hand.

        `object_type` takes the usual name (contacts | companies | deals |
        tickets) and is translated to the numeric objectTypeId lists key on. For
        a custom object, pass its raw id (`"2-7"`).

        Ops:
        - **"search"** (default) : find lists by `name` fragment via `query`,
          optionally narrowed by `object_type`.
        - **"get"** : one list, by `list_id` — or by `name` + `object_type`.
          `include_filters=true` also returns its criteria tree.
        - **"create"** : create a list. Requires `name` + `object_type`. For a
          DYNAMIC/SNAPSHOT list, pass `filter_branch`.
        - **"update"** : rename (`name`) and/or replace the criteria
          (`filter_branch`). Requires `list_id`.
        - **"delete"** : delete a list — restorable for 90 days. Requires
          `list_id`. Supports `dry_run`.
        - **"restore"** : restore a deleted list (within those 90 days).
        - **"members"** : the record ids in a list (paginated, `after`).
        - **"add_members"** / **"remove_members"** : add/remove `record_ids`.
          On op="add_members", also passing `remove_record_ids` does both in a
          SINGLE list revision (one recompute instead of two).
        - **"clear_members"** : remove EVERY record from the list (the list
          survives). Supports `dry_run` — use it.
        - **"copy_from"** : copy every member of `source_list_id` into
          `list_id` (HubSpot caps this at 100 000 records).
        - **"record_lists"** : which lists a given record belongs to. Requires
          `object_type` + `record_id`.

        ⚠️ Requires the `crm.lists.read` / `crm.lists.write` scopes on the
        private app. A token created before these scopes existed answers 403 —
        that means "add the scope", not "the key is wrong".

        Args:
            op: search | get | create | update | delete | restore | members |
                add_members | remove_members | clear_members | copy_from |
                record_lists.
            list_id: the list — required for every op except search, create and
                record_lists.
            object_type: contacts | companies | deals | tickets, or a raw
                objectTypeId ("2-7") for a custom object.
            name: op="create" the list name ; op="update" the new name ;
                op="get" look the list up by name (with `object_type`).
            processing_type: op="create" — MANUAL (default) | DYNAMIC | SNAPSHOT.
            filter_branch: op="create"/"update" — HubSpot's criteria tree, passed
                through verbatim. Recursive shape: {"filterBranchType": "OR",
                "filterBranches": [{"filterBranchType": "AND", "filters": [
                {"filterType": "PROPERTY", "property": "<internal name>",
                "operation": {"operationType": "NUMBER", "operator":
                "IS_GREATER_THAN_OR_EQUAL_TO", "value": 12}}]}]}. Property names
                are the INTERNAL ones — get them from `hubspot_property`.
            record_ids: op="add_members"/"remove_members" — the record ids to
                add / to remove.
            remove_record_ids: op="add_members" only — ids to remove in the same
                revision as the ones being added.
            record_id: op="record_lists" — the single record to look up.
            source_list_id: op="copy_from" — the list to copy members from.
            query: op="search" — name fragment.
            include_filters: op="get" — also return the list's criteria tree.
            limit: op="members" — page size.
            after: op="members" — pagination cursor from a previous response.
            dry_run: op="delete"/"clear_members"/"remove_members" — validate and
                report what WOULD change (with the list's current state), without
                writing.
        """
        c = _client()

        if op == "search":
            return c.search_lists(
                query=query,
                object_type_id=(_object_type_id(object_type, op)
                                if object_type else None))

        if op == "get":
            if list_id:
                return c.get_list(list_id, include_filters=include_filters)
            if name and object_type:
                return c.get_list_by_name(
                    _object_type_id(object_type, op), name,
                    include_filters=include_filters)
            raise _bad("op='get' requiert list_id, ou name + object_type")

        if op == "create":
            if processing_type != "MANUAL" and filter_branch is None:
                raise _bad(
                    f"processing_type='{processing_type}' requiert filter_branch "
                    "(une liste sans critères n'aurait aucun membre)")
            return c.create_list(
                _need(name, "name", op),
                _object_type_id(object_type, op),
                processing_type=processing_type,
                filter_branch=filter_branch)

        if op == "update":
            lid = _need(list_id, "list_id", op)
            if name is None and filter_branch is None:
                raise _bad("op='update' requiert name et/ou filter_branch")
            out: dict = {}
            if name is not None:
                out["renamed"] = c.update_list_name(lid, name)
            if filter_branch is not None:
                out["filters"] = c.update_list_filters(lid, filter_branch)
            return out

        if op == "delete":
            lid = _need(list_id, "list_id", op)
            if dry_run:
                return {"dry_run": True, "would": "delete", "list_id": lid,
                        "current": c.get_list(lid),
                        "note": "restaurable 90 jours via op='restore'"}
            return c.delete_list(lid)

        if op == "restore":
            return c.restore_list(_need(list_id, "list_id", op))

        if op == "members":
            return c.get_list_memberships(
                _need(list_id, "list_id", op), limit=limit, after=after)

        if op == "add_members":
            lid = _need(list_id, "list_id", op)
            ids = _ids(record_ids, "record_ids", op)
            _writable_list(c, lid, op)
            if remove_record_ids:
                return c.add_and_remove_list_memberships(
                    lid, record_ids_to_add=ids,
                    record_ids_to_remove=_ids(
                        remove_record_ids, "remove_record_ids", op))
            return c.add_list_memberships(lid, ids)

        if op == "remove_members":
            lid = _need(list_id, "list_id", op)
            ids = _ids(record_ids, "record_ids", op)
            info = _writable_list(c, lid, op)
            if dry_run:
                return {"dry_run": True, "would": "remove_members",
                        "list_id": lid, "record_ids": ids, "current": info}
            return c.remove_list_memberships(lid, ids)

        if op == "clear_members":
            lid = _need(list_id, "list_id", op)
            info = _writable_list(c, lid, op)
            if dry_run:
                return {"dry_run": True, "would": "clear_members",
                        "list_id": lid, "current": info,
                        "note": "retire TOUS les membres ; la liste survit"}
            return c.delete_all_list_memberships(lid)

        if op == "copy_from":
            lid = _need(list_id, "list_id", op)
            src = _need(source_list_id, "source_list_id", op)
            _writable_list(c, lid, op)
            return c.add_memberships_from_list(lid, src)

        if op == "record_lists":
            return c.get_record_memberships(
                _object_type_id(object_type, op),
                _need(record_id, "record_id", op))

        raise _bad("op doit être 'search', 'get', 'create', 'update', 'delete', "
                   "'restore', 'members', 'add_members', 'remove_members', "
                   "'clear_members', 'copy_from' ou 'record_lists'")

    @mcp.tool()
    def hubspot_property(
        op: Literal["list", "get", "create", "update", "delete", "groups"] = "list",
        object_type: Optional[str] = None,
        property_name: Optional[str] = None,
        definition: Optional[dict] = None,
        archived: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """HubSpot properties — the field schema of a CRM object type.

        Read this BEFORE writing anything. HubSpot's internal property names are
        not the labels shown in the UI (`dealstage`, not "Deal Stage"), and an
        enumeration property only accepts its declared `options[].value` — so a
        create/update written from the label is a guess that fails or, worse,
        silently writes nothing. List criteria (`hubspot_list`'s `filter_branch`)
        reference the same internal names.

        ⚠️ One enumeration is NOT self-describing here: `dealstage` comes back
        with an EMPTY `options` list, because deal stages belong to a pipeline,
        not to the property. Reading this tool is not enough to write a deal
        stage — that needs the Pipelines API, which this connector does not
        expose yet.

        Ops:
        - **"list"** (default) : every property of `object_type`, with its type,
          fieldType and enumeration options.
        - **"get"** : one property, by internal `property_name`.
        - **"create"** : create a property. Requires `definition` — at minimum
          {"name", "label", "type", "fieldType", "groupName"}, plus "options"
          ([{"label", "value"}]) for an enumeration.
        - **"update"** : PATCH a property (e.g. add options). Requires
          `property_name` + `definition`.
        - **"delete"** : archive a property. Requires `property_name`. Supports
          `dry_run`.
        - **"groups"** : the property groups (the tabs of a record page).

        Args:
            op: list | get | create | update | delete | groups.
            object_type: contacts | companies | deals | tickets (or a custom
                object's name). Required for every op.
            property_name: the INTERNAL name — required for get, update, delete.
            definition: op="create"/"update" — the property definition dict.
            archived: op="list"/"get" — return archived properties instead.
            dry_run: op="delete" — report the property that would be archived
                without archiving it.
        """
        c = _client()

        if op == "list":
            return c.list_properties(
                _need(object_type, "object_type", op), archived=archived)

        if op == "get":
            return c.get_property(
                _need(object_type, "object_type", op),
                _need(property_name, "property_name", op), archived=archived)

        if op == "create":
            return c.create_property(
                _need(object_type, "object_type", op),
                _payload(definition, "definition", op))

        if op == "update":
            return c.update_property(
                _need(object_type, "object_type", op),
                _need(property_name, "property_name", op),
                _payload(definition, "definition", op))

        if op == "delete":
            otype = _need(object_type, "object_type", op)
            pname = _need(property_name, "property_name", op)
            if dry_run:
                return {"dry_run": True, "would": "delete", "object_type": otype,
                        "property_name": pname,
                        "current": c.get_property(otype, pname)}
            return c.delete_property(otype, pname)

        if op == "groups":
            return c.list_property_groups(_need(object_type, "object_type", op))

        raise _bad("op doit être 'list', 'get', 'create', 'update', 'delete' "
                   "ou 'groups'")

    @mcp.tool()
    def hubspot_owners() -> dict:
        """List HubSpot owners (users) — to assign records by ownerId."""
        return _client().list_owners()
