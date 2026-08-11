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
"""
from __future__ import annotations

from typing import Optional, Union

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
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
        op: str = "search",
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

    @mcp.tool()
    def hubspot_owners() -> dict:
        """List HubSpot owners (users) — to assign records by ownerId."""
        return _client().list_owners()
