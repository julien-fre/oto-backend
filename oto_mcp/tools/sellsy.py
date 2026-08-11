"""Sellsy — CRM + gestion commerciale FR (api.sellsy.com/v2).

Un même compte Sellsy tient la relation client (sociétés, particuliers, contacts,
opportunités) ET la chaîne de vente (devis → commande → facture → avoir,
encaissements, catalogue). Le connecteur expose les deux.

**Surface consolidée (ADR 0047)** : un tool par OBJET métier, le verbe en `op`.
Deux objets sont portés par plusieurs ressources de l'API et tiennent donc dans un
seul tool à `kind=`, parce que leurs PARAMÈTRES se recouvrent exactement (critère
de fusion de l'ADR — pas le comptage) :
- `sellsy_document(kind=…)` — devis/commande/facture/avoir ont les MÊMES paramètres
  (mêmes filtres de recherche, même corps `related`/`rows`) ; les séparer aurait
  quadruplé un bloc identique sans rien apprendre à l'agent.
- `sellsy_third_party(kind=…)` — société et particulier sont les deux faces du même
  rôle : le tiers qu'un document facture (`related` porte d'ailleurs le
  discriminant, `{"id": 42, "type": "company"}`). Mêmes verbes, mêmes filtres, même
  corps ; seul `link_contact`/`unlink_contact` est propre à la société.

Restent des tools nommés là où les verbes ne se factorisent pas : `sellsy_contact`
(la personne, pas le tiers : pas de `convert`, pas d'encaissement), `sellsy_ref`
(lecture seule, ni `op` ni pagination) et `sellsy_search` (plein texte, `q` seul).

Credential = OAuth2 client_credentials multi-champs (client_id + client_secret,
créés dans Réglages → Portail développeur → API V2), résolu par appel via
`access.resolve_credential_fields("sellsy")`. byo-only : chaque org connecte SON
compte Sellsy, il n'y a pas de clé plateforme à partager.

Deux gardes valent d'être connues avant d'écrire :
- **`op="create"` accepte `dry_run=True`** (paramètre `verify` de l'API) : Sellsy
  valide le payload et ne persiste rien — le bon réflexe avant une création en
  volume, les champs obligatoires variant d'un compte à l'autre (champs
  personnalisés, numérotation).
- **valider un document est irréversible** : `op="validate"` sort une facture ou
  un avoir de l'état brouillon, lui donne son numéro définitif et le rend
  comptable. À n'appeler qu'après validation humaine.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify

# Les deux faces du tiers : nom d'agent → ressource de l'API.
_THIRD_PARTIES = {
    "company": "companies",
    "individual": "individuals",
}

# Les quatre documents de vente : nom d'agent → ressource de l'API.
_DOCUMENTS = {
    "estimate": "estimates",
    "invoice": "invoices",
    "order": "orders",
    "credit_note": "credit-notes",
}

# Référentiels lisibles sans écriture — `sellsy_ref(kind=…)` → chemin API.
_REFS = {
    "staffs": "staffs",
    "custom_fields": "custom-fields",
    "pipelines": "opportunities/pipelines",
    "sources": "opportunities/sources",
    "categories": "opportunities/categories",
    "payment_methods": "payments/methods",
    "taxes": "taxes",
    "units": "units",
    "currencies": "currencies",
    "countries": "countries",
    "rate_categories": "rate-categories",
    "accounting_codes": "accounting-codes",
    "task_labels": "tasks/labels",
    "document_layouts": "document-layouts",
}

_COMMON_OPS = ("list", "search", "get", "create", "update", "delete",
               "custom_fields")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Sellsy a rejeté l'accès (HTTP {status}) — vérifie le client_id / "
                "client_secret du connecteur, et que l'accès API V2 porte bien les "
                f"droits (scopes) de cette opération. {e.body}")
    if status == 402:
        return ("Sellsy : quota du plan atteint sur cette ressource (402) — la "
                f"création est bloquée côté abonnement. {e.body}")
    if status == 404:
        return f"Sellsy : objet introuvable (404) — vérifie l'id. {e.body}"
    if status == 409:
        return f"Sellsy : conflit avec l'état actuel de l'objet (409). {e.body}"
    if status == 429:
        return ("Sellsy : quota de requêtes épuisé (429) — les quotas sont comptés "
                "par seconde/minute/jour/mois, réessaie plus tard ou réduis la "
                "pagination (limit, all_pages).")
    if status in (500, 502, 503, 504):
        return f"Sellsy est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Sellsy a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : frappe un jeton puis lit UN collaborateur —
    l'appel authentifié le moins coûteux, sans effet de bord ni donnée requise."""
    from oto.tools.sellsy import SellsyClient
    SellsyClient(client_id=fields.get("client_id"),
                 client_secret=fields.get("client_secret")).list_records(
                     "staffs", limit=1)


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.sellsy import SellsyClient

    connector_verify.register("sellsy", _verify)

    def _client() -> SellsyClient:
        creds = access.resolve_credential_fields("sellsy")
        return SellsyClient(client_id=creds.get("client_id"),
                            client_secret=creds.get("client_secret"))

    @contextmanager
    def _upstream():
        """Traduit un refus de Sellsy en erreur d'outil actionnable."""
        try:
            yield
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _need(value, name: str, op: str):
        """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback."""
        if value is None:
            raise _bad(f"op='{op}' requiert {name}")
        return value

    def _crud(c, resource: str, op: str, *, record_id=None, data=None,
              filters=None, limit=None, offset=None, order=None, direction=None,
              fields=None, embed=None, all_pages=False, max_pages=10,
              dry_run=None, extra_ops=()) -> Any:
        """Les verbes que TOUTE ressource Sellsy expose de la même façon.

        Renvoie None quand `op` appartient à `extra_ops` (verbe propre au tool
        appelant, qui prend alors le relais) ; refuse tout autre `op`.
        """
        if op == "list":
            if all_pages:
                return c.list_all(resource, max_pages=max_pages,
                                  fields=fields, embed=embed)
            return c.list_records(resource, limit=limit, offset=offset, order=order,
                                  direction=direction, fields=fields, embed=embed)
        if op == "search":
            if all_pages:
                return c.list_all(resource, filters=filters or {},
                                  max_pages=max_pages, fields=fields, embed=embed)
            return c.search_records(resource, filters or {}, limit=limit,
                                    offset=offset, order=order, direction=direction,
                                    fields=fields, embed=embed)
        if op == "get":
            return c.get_record(resource, _need(record_id, "record_id", op),
                                fields=fields, embed=embed)
        if op == "create":
            return c.create_record(resource, _need(data, "data", op),
                                   embed=embed, verify=dry_run)
        if op == "update":
            return c.update_record(resource, _need(record_id, "record_id", op),
                                   _need(data, "data", op), embed=embed)
        if op == "delete":
            return c.delete_record(resource, _need(record_id, "record_id", op))
        if op == "custom_fields":
            record_id = _need(record_id, "record_id", op)
            if data is not None:
                return c.set_custom_fields(resource, record_id,
                                           data.get("custom_fields", []))
            return c.get_custom_fields(resource, record_id)
        if op not in extra_ops:
            raise _bad(f"op inconnu: {op!r} — attendus: "
                       + ", ".join(_COMMON_OPS + tuple(extra_ops)))
        return None

    # --- CRM : tiers et contacts --------------------------------------------

    @mcp.tool()
    def sellsy_third_party(
        kind: str, op: str = "list", record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        contact_id: Optional[int] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Tiers du CRM : sociétés et particuliers (clients, prospects, fournisseurs).

        `kind` ∈ "company" (société) | "individual" (particulier) — les deux faces
        du tiers, mêmes verbes et mêmes paramètres. Le particulier est le pendant
        « personne physique » de la société : un devis ou une facture se rattache
        SOIT à une société, SOIT à un particulier — c'est ici que vivent les clients
        qui ne sont pas des entreprises (le `related` d'un document porte le même
        discriminant : `[{"id": 42, "type": "company"}]`).

        `op` :
        - "list" / "search" : liste et liste filtrée. Filtres utiles :
          `{"name": "acme"}`, `{"type": ["client"]}`, `{"created": {"start":
          "2026-01-01T00:00:00+01:00"}}`, `{"postal_code": ["13001"]}` ; côté
          particulier aussi `{"email": …}`.
        - "get" / "create" / "update" / "delete" (`record_id`, `data`).
          Créer exige `type` ∈ prospect | client | supplier, plus `name` pour une
          société et `last_name` pour un particulier.
        - "contacts" : les contacts rattachés au tiers.
        - "convert" : bascule un prospect en client (irréversible côté Sellsy).
        - "link_contact" / "unlink_contact" (`contact_id`) : rattache ou détache
          un contact existant. **kind="company" seulement** — un contact se
          rattache à une société, pas à un particulier.
        - "custom_fields" : lit les champs personnalisés ; avec
          `data={"custom_fields": [{"id": 12, "value": "x"}]}`, les écrit.
        - "record_payment" (`data`) : encaissement sur le compte du tiers
          (`{"amount": {"value": "120.00", "currency": "EUR"}, "paid_at": …,
          "payment_method_id": …, "type": "credit"}`).

        Args:
            kind: le type de tiers (ci-dessus). op: le verbe.
            record_id: id du tiers (société ou particulier).
            data: corps de l'écriture. filters: filtres d'op="search".
            contact_id: op link_contact / unlink_contact (kind="company").
            limit: taille de page (max 100). offset: curseur `pagination.offset`
                rendu par la page précédente. order / direction: tri (asc | desc).
            fields: projection (`["id", "name"]`). embed: objets liés à inclure.
            all_pages / max_pages: déroule la pagination (1 requête par page).
            dry_run: op="create" — valide le payload SANS rien persister.
        """
        resource = _THIRD_PARTIES.get(kind)
        if resource is None:
            raise _bad(f"kind doit être l'un de {', '.join(_THIRD_PARTIES)}")
        extra = ("contacts", "convert", "link_contact", "unlink_contact",
                 "record_payment")
        c = _client()
        with _upstream():
            out = _crud(c, resource, op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=extra)
            if out is not None:
                return out
            if op == "contacts":
                return c.list_sub(resource, _need(record_id, "record_id", op),
                                  "contacts", limit=limit, offset=offset)
            if op == "convert":
                return c.act(resource, _need(record_id, "record_id", op),
                             "convert", payload=data or {"target": "client"})
            if op in ("link_contact", "unlink_contact"):
                if kind != "company":
                    raise _bad(f"op='{op}' ne s'applique qu'à kind='company' — un "
                               "contact se rattache à une société, pas à un "
                               "particulier")
                company_id = _need(record_id, "record_id", op)
                contact_id = _need(contact_id, "contact_id", op)
                if op == "link_contact":
                    return c.link_contact_to_company(company_id, contact_id,
                                                     payload=data)
                return c.unlink_contact_from_company(company_id, contact_id)
            return c.act(resource, _need(record_id, "record_id", op),
                         "payments", payload=_need(data, "data", op))

    @mcp.tool()
    def sellsy_contact(
        op: str = "list", record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Contacts — les personnes rattachées aux sociétés et particuliers.

        Un contact existe indépendamment du tiers : le rattachement se fait par
        `sellsy_third_party(kind="company", op="link_contact")`.

        `op` : "list" / "search" (filtres `last_name`, `email`, `phone_number`,
        `companies`, `is_linked`…), "get" / "create" / "update" / "delete",
        "companies" (les sociétés du contact), "custom_fields".

        Args:
            op: le verbe (ci-dessus). record_id: id du contact.
            data: corps de l'écriture. filters: filtres d'op="search".
            limit: taille de page (max 100). offset: curseur `pagination.offset`
                rendu par la page précédente. order / direction: tri (asc | desc).
            fields: projection. embed: objets liés à inclure.
            all_pages / max_pages: déroule la pagination (1 requête par page).
            dry_run: op="create" — valide le payload SANS rien persister.
        """
        c = _client()
        with _upstream():
            out = _crud(c, "contacts", op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("companies",))
            if out is not None:
                return out
            return c.list_sub("contacts", _need(record_id, "record_id", op),
                              "companies", limit=limit, offset=offset)

    @mcp.tool()
    def sellsy_opportunity(
        op: str = "list", record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        step: Optional[int] = None, before_sibling: Optional[int] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Opportunités — le pipeline commercial.

        `op` :
        - "list" / "search" : filtres `{"pipeline": [id]}`, `{"step": [id]}`,
          `{"statuses": ["open"]}`, `{"due_date": {"start": …, "end": …}}`,
          `{"assigned_staffs": [id]}`, `{"amount": {"min": …, "max": …}}`.
        - "get" / "create" / "update" / "delete". Créer demande `name`,
          `pipeline`, `step` et le tiers en `related`
          (`[{"id": 42, "type": "company"}]`) — les ids de pipeline et d'étape se
          lisent avec `sellsy_ref(kind="pipelines" | "steps")`.
        - "move" (`step`, option `before_sibling`) : déplace l'opportunité dans le
          pipeline — c'est l'endpoint dédié, `op="update"` ne change pas l'étape.
        - "custom_fields".

        Args:
            op: le verbe (ci-dessus). record_id: id de l'opportunité.
            data: corps de l'écriture. filters: filtres d'op="search".
            step: op="move" — id de l'étape de destination.
            before_sibling: op="move" — se place avant cette opportunité (sinon en
                dernier rang de l'étape).
            limit: taille de page (max 100). offset: curseur `pagination.offset`
                rendu par la page précédente. order / direction: tri (asc | desc).
            fields: projection. embed: objets liés à inclure.
            all_pages / max_pages: déroule la pagination (1 requête par page).
            dry_run: op="create" — valide le payload SANS rien persister.
        """
        c = _client()
        with _upstream():
            out = _crud(c, "opportunities", op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("move",))
            if out is not None:
                return out
            payload = {"step": _need(step, "step", op)}
            if before_sibling is not None:
                payload["before_sibling"] = before_sibling
            return c.act("opportunities", _need(record_id, "record_id", op),
                         "step-rank", payload=payload, method="PATCH")

    # --- chaîne de vente -----------------------------------------------------

    @mcp.tool()
    def sellsy_document(
        kind: str, op: str = "list", record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Documents de vente : devis, commandes, factures, avoirs.

        `kind` ∈ "estimate" | "order" | "invoice" | "credit_note" — mêmes verbes
        et mêmes paramètres pour les quatre.

        `op` :
        - "list" / "search" : filtres `{"status": ["due"]}`, `{"number": "F-2026"}`,
          `{"date": {"start": "2026-01-01", "end": "2026-01-31"}}`,
          `{"related_objects": [{"id": 42, "type": "company"}]}`,
          `{"owners": [id]}`, `{"currency": ["EUR"]}`.
        - "get" / "create" / "update" (`record_id`, `data`). Un document créé est
          un BROUILLON. Corps minimal : `related` (le tiers,
          `[{"id": 42, "type": "company"}]`, exactement une société OU un
          particulier), `date`, `subject`, `currency`, et `rows` — chaque ligne
          porte son `type` : `single` (libre : `quantity`, `unit_amount`,
          `tax_id`), `catalog` (article du catalogue : `related` + `quantity`),
          `title`, `comment`, `sub-total`, `break-line`.
        - "validate" (facture, avoir) : **irréversible** — sort le document du
          brouillon, fige son numéro et le rend comptable. `data` peut porter la
          `date` de validation.
        - "status" (devis, `status`) : draft, sent, read, accepted, refused,
          expired, cancelled.
        - "payments" : les encaissements rattachés au document.
        - "linked" : les avoirs d'une facture, ou les factures d'un avoir.
        - "custom_fields".

        Args:
            kind: le type de document (ci-dessus). op: le verbe.
            record_id: id du document. data: corps de l'écriture.
            filters: filtres d'op="search". status: op="status" — nouveau statut.
            limit: taille de page (max 100). offset: curseur `pagination.offset`
                rendu par la page précédente. order / direction: tri (asc | desc).
            fields: projection. embed: objets liés à inclure.
            all_pages / max_pages: déroule la pagination (1 requête par page).
            dry_run: op="create" — valide le payload SANS rien persister.
        """
        resource = _DOCUMENTS.get(kind)
        if resource is None:
            raise _bad(f"kind doit être l'un de {', '.join(_DOCUMENTS)}")
        c = _client()
        with _upstream():
            out = _crud(c, resource, op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("validate", "status", "payments", "linked"))
            if out is not None:
                return out
            record_id = _need(record_id, "record_id", op)
            if op == "validate":
                if kind not in ("invoice", "credit_note"):
                    raise _bad("op='validate' ne s'applique qu'à kind='invoice' "
                               "ou 'credit_note' (un devis change d'état par "
                               "op='status')")
                return c.act(resource, record_id, "validate", payload=data or {})
            if op == "status":
                if kind != "estimate":
                    raise _bad("op='status' ne s'applique qu'à kind='estimate'")
                return c.act(resource, record_id, "status",
                             payload={"status": _need(status, "status", op)},
                             method="PUT")
            if op == "payments":
                return c.list_sub(resource, record_id, "payments", limit=limit,
                                  offset=offset)
            sub = {"invoice": "credit-notes", "credit_note": "invoices"}.get(kind)
            if sub is None:
                raise _bad("op='linked' ne s'applique qu'à kind='invoice' "
                           "(ses avoirs) ou 'credit_note' (ses factures)")
            return c.list_sub(resource, record_id, sub, limit=limit, offset=offset)

    @mcp.tool()
    def sellsy_payment(
        op: str = "list", record_id: Optional[int] = None,
        filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
    ) -> Any:
        """Encaissements — ce qui a été payé, et sur quel document.

        `op` : "list" / "search" (filtres `{"status": [...]}`,
        `{"related_objects": [{"id": 9, "type": "invoice"}]}`), "get", "delete".

        Enregistrer un paiement se fait sur le tiers :
        `sellsy_third_party(op="record_payment")`, kind="company" ou "individual".

        Args:
            op: le verbe (ci-dessus). record_id: id du paiement.
            filters: filtres d'op="search".
            limit: taille de page (max 100). offset: curseur `pagination.offset`
                rendu par la page précédente. order / direction: tri (asc | desc).
            fields: projection. embed: objets liés à inclure.
            all_pages / max_pages: déroule la pagination (1 requête par page).
        """
        c = _client()
        with _upstream():
            return _crud(c, "payments", op, record_id=record_id, filters=filters,
                         limit=limit, offset=offset, order=order,
                         direction=direction, fields=fields, embed=embed,
                         all_pages=all_pages, max_pages=max_pages)

    @mcp.tool()
    def sellsy_item(
        op: str = "list", record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Catalogue — produits, services, forfaits de livraison.

        C'est ici que se lisent les ids d'article à mettre dans une ligne
        `type="catalog"` d'un document.

        `op` : "list" / "search" (filtres `{"name": …}`, `{"reference": …}`,
        `{"type": ["product", "service"]}`, `{"is_archived": false}`),
        "get" / "create" / "update" / "delete" (créer exige `type` et
        `reference`), "prices" (grille tarifaire de l'article).

        Args:
            op: le verbe (ci-dessus). record_id: id de l'article.
            data: corps de l'écriture. filters: filtres d'op="search".
            limit: taille de page (max 100). offset: curseur `pagination.offset`
                rendu par la page précédente. order / direction: tri (asc | desc).
            fields: projection. embed: objets liés à inclure.
            all_pages / max_pages: déroule la pagination (1 requête par page).
            dry_run: op="create" — valide le payload SANS rien persister.
        """
        c = _client()
        with _upstream():
            out = _crud(c, "items", op, record_id=record_id, data=data,
                        filters=filters, limit=limit, offset=offset, order=order,
                        direction=direction, fields=fields, embed=embed,
                        all_pages=all_pages, max_pages=max_pages, dry_run=dry_run,
                        extra_ops=("prices",))
            if out is not None:
                return out
            return c.list_sub("items", _need(record_id, "record_id", op), "prices",
                              limit=limit, offset=offset)

    # --- suivi ---------------------------------------------------------------

    @mcp.tool()
    def sellsy_task(
        op: str = "list", record_id: Optional[int] = None,
        data: Optional[dict] = None, filters: Optional[dict] = None,
        limit: Optional[int] = None, offset: Optional[str] = None,
        order: Optional[str] = None, direction: Optional[str] = None,
        fields: Optional[list] = None, embed: Optional[list] = None,
        all_pages: bool = False, max_pages: int = 10,
        dry_run: Optional[bool] = None,
    ) -> Any:
        """Tâches — les relances et actions rattachées à un tiers ou un document.

        `op` : "list" / "search" (filtres `{"assigned_staffs": [id]}`,
        `{"due_date": {"start": …, "end": …}}`, `{"statuses": ["todo"]}`,
        `{"companies": [id]}`), "get" / "create" / "update" / "delete".

        Créer exige `related` (`[{"id": 42, "type": "company"}]`) ; les champs
        usuels sont `title`, `due_date`, `assigned_staff_ids`, `priority`.

        Args:
            op: le verbe (ci-dessus). record_id: id de la tâche.
            data: corps de l'écriture. filters: filtres d'op="search".
            limit: taille de page (max 100). offset: curseur `pagination.offset`
                rendu par la page précédente. order / direction: tri (asc | desc).
            fields: projection. embed: objets liés à inclure.
            all_pages / max_pages: déroule la pagination (1 requête par page).
            dry_run: op="create" — valide le payload SANS rien persister.
        """
        c = _client()
        with _upstream():
            return _crud(c, "tasks", op, record_id=record_id, data=data,
                         filters=filters, limit=limit, offset=offset, order=order,
                         direction=direction, fields=fields, embed=embed,
                         all_pages=all_pages, max_pages=max_pages, dry_run=dry_run)

    # --- référentiels & recherche transverse ---------------------------------

    @mcp.tool()
    def sellsy_ref(kind: str, pipeline_id: Optional[int] = None,
                   linked_type: Optional[str] = None,
                   limit: Optional[int] = None) -> Any:
        """Référentiels du compte en LECTURE SEULE — les ids à résoudre AVANT
        d'écrire (ne jamais deviner un id d'étape, de taxe ou de collaborateur).

        `kind` :
        - "staffs" : collaborateurs (owner_id, assigned_staff_ids).
        - "pipelines" : pipelines d'opportunités ; "steps" (`pipeline_id`) :
          leurs étapes ; "sources" / "categories" : origine des opportunités.
        - "custom_fields" : champs personnalisés du compte (id + type + code).
        - "taxes" : taux de TVA (`tax_id` d'une ligne) ; "units" : unités ;
          "currencies" ; "countries" ; "payment_methods" ; "rate_categories" ;
          "accounting_codes" ; "task_labels" ; "document_layouts".
        - "smart_tags" (`linked_type`) : étiquettes existantes pour un type
          d'objet (company, individual, contact, opportunity, invoice…).

        Args:
            kind: le référentiel voulu.
            pipeline_id: kind="steps" — les étapes de CE pipeline.
            linked_type: kind="smart_tags" — le type d'objet porteur.
            limit: taille de page.
        """
        c = _client()
        with _upstream():
            if kind == "smart_tags":
                return c.smart_tags_autocomplete(
                    _need(linked_type, "linked_type", "smart_tags"))
            if kind == "steps":
                pipeline_id = _need(pipeline_id, "pipeline_id", "steps")
                return c.list_records(
                    f"opportunities/pipelines/{pipeline_id}/steps", limit=limit)
            path = _REFS.get(kind)
            if path is None:
                raise _bad("kind doit être l'un de "
                           + ", ".join(list(_REFS) + ["steps", "smart_tags"]))
            return c.list_records(path, limit=limit)

    @mcp.tool()
    def sellsy_search(q: str, types: Optional[list] = None,
                      limit: Optional[int] = None,
                      archived: Optional[bool] = None) -> Any:
        """Recherche plein-texte transverse — retrouver un objet quand on ne sait
        pas dans quelle table il vit (« qui est Acme chez nous ? »).

        Pour filtrer finement (dates, statuts, montants), passer par le
        `op="search"` de l'objet concerné : celui-ci ne fait que du plein-texte.

        Args:
            q: le texte cherché (nom, email, numéro de document…).
            types: restreint aux types voulus — `company`, `company.client`,
                `company.prospect`, `individual`, `contact`, `opportunity`,
                `item`, `purchase`…
            limit: nombre de résultats (max 100).
            archived: inclure les objets archivés.
        """
        with _upstream():
            return _client().global_search(q, types=types, limit=limit,
                                           archived=archived)
