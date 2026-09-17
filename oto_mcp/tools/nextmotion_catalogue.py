"""Nextmotion — le catalogue d'une clinique : ce qui se réserve, se vend, et à quel prix.

Module frère de `nextmotion.py` (cf. `Connector.modules`) : même clé, même client. Un
seul outil, `nextmotion_catalog`, parce que tous ses objets partagent list/get sous une
clinique (ADR 0047) ; chaque `kind` porte au plus un filtre à lui, refusé aux autres.

Deux ops de plus que list/get, sur l'objet qu'elles détaillent :
- `items` — les soins d'un forfait (`treatment_package`), paginés ;
- `distributions` — la répartition comptable par utilisateur d'un tarif
  (`treatment_pricing`) ou d'un forfait, non paginée.

Rien ici ne porte de patient. La liste blanche (`nextmotion_socle`) retire quand même
les fichiers joints et les questionnaires (`bolt_note`, `survey_form`) qu'un type de
visite ou un tarif référence.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .nextmotion_garde import Kind, _bad, _client, _need, _paging, _refuse_ignored, _run
from .nextmotion_garde import _serve_kind
from .nextmotion_socle import (_ACCOUNTING_DISTRIBUTION, _CATEGORY, _GLOBAL_PRODUCT,
                               _PACKAGE, _PACKAGE_ITEM, _PRICING, _SUB_VISIT_TYPE,
                               _TREATMENT_TYPE, _USER_DISTRIBUTION, _VISIT_TYPE, _page,
                               _shape)

_KINDS = {
    "visit_type": Kind(
        "visit_types", _shape(_VISIT_TYPE),
        lambda c, cid, visit_type_category_id, **p: c.list_visit_types(
            cid, visit_type_category_id=visit_type_category_id, **p),
        lambda c, i: c.get_visit_type(i), ("visit_type_category_id",)),
    "visit_type_category": Kind(
        "visit_type_categories", _shape(_CATEGORY),
        lambda c, cid, **p: c.list_visit_type_categories(cid, **p),
        lambda c, i: c.get_visit_type_category(i)),
    "sub_visit_type": Kind(
        "sub_visit_types", _shape(_SUB_VISIT_TYPE),
        lambda c, cid, visit_type_id, **p: c.list_sub_visit_types(
            cid, visit_type_id=visit_type_id, **p),
        lambda c, i: c.get_sub_visit_type(i), ("visit_type_id",)),
    "treatment_type": Kind(
        "treatment_types", _shape(_TREATMENT_TYPE),
        lambda c, cid, search, **p: c.list_treatment_types(cid, search=search, **p),
        lambda c, i: c.get_treatment_type(i), ("search",)),
    "treatment_pricing": Kind(
        "treatment_pricings", _shape(_PRICING),
        lambda c, cid, treatment_type_id, **p: c.list_treatment_pricings(
            cid, treatment_type_id=treatment_type_id, **p),
        lambda c, i: c.get_treatment_pricing(i), ("treatment_type_id",)),
    "treatment_package": Kind(
        "treatment_packages", _shape(_PACKAGE),
        lambda c, cid, search, **p: c.list_treatment_packages(cid, search=search, **p),
        lambda c, i: c.get_treatment_package(i), ("search",)),
    "accounting_distribution": Kind(
        "accounting_distributions", _shape(_ACCOUNTING_DISTRIBUTION),
        lambda c, cid, **p: c.list_accounting_distributions(cid, **p),
        lambda c, i: c.get_accounting_distribution(i)),
    "global_product": Kind(
        "global_products", _shape(_GLOBAL_PRODUCT),
        lambda c, cid, search, **p: c.list_global_products(cid, search=search, **p),
        None, ("search",)),
}

_DISTRIBUTIONS = {
    "treatment_pricing": lambda c, i: c.list_treatment_pricing_distributions(i),
    "treatment_package": lambda c, i: c.list_treatment_package_distributions(i),
}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_catalog(
        kind: Literal["visit_type", "visit_type_category", "sub_visit_type",
                      "treatment_type", "treatment_pricing", "treatment_package",
                      "accounting_distribution", "global_product"],
        op: Literal["list", "get", "items", "distributions"] = "list",
        clinic_id: Optional[str] = None,
        item_id: Optional[str] = None,
        visit_type_category_id: Optional[str] = None,
        visit_type_id: Optional[str] = None,
        treatment_type_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """The service catalogue of a Nextmotion clinic: what can be booked or sold,
        at what price, and how the price is split for accounting.

        `kind`:
        - "visit_type" — bookable appointment types (duration, price, variants);
          filter `visit_type_category_id`.
        - "visit_type_category" — their categories.
        - "sub_visit_type" — variants of a visit type; filter `visit_type_id`.
        - "treatment_type" — treatments offered, with their pricings; filter `search`.
        - "treatment_pricing" — prices, VAT, sub-pricings, accounting codes, linked
          products; filter `treatment_type_id`.
        - "treatment_package" — packages of sessions (price, VAT, distribution);
          filter `search`.
        - "accounting_distribution" — clinic/provider split models.
        - "global_product" — the product catalogue (name, brand); filter `search`;
          list only (no detail endpoint).

        `op`:
        - **"list"** (default, needs `clinic_id`).
        - **"get"** (`item_id`).
        - **"items"** — kind="treatment_package": the treatments of package `item_id`
          (paginated).
        - **"distributions"** — kind="treatment_pricing" or "treatment_package": the
          per-user accounting distribution of `item_id` (unpaginated).
        Each filter belongs to one kind only; the others refuse it.

        Args:
            kind: which catalogue.
            op: list (default) | get | items | distributions.
            clinic_id: op="list" — the clinic.
            item_id: op="get"/"items"/"distributions" — the item of that kind.
            visit_type_category_id / visit_type_id / treatment_type_id / search:
                op="list" — the kind's filter (see above).
            limit / offset: op="list"/"items" — pagination (limit 1..100, default 50).
            fields: op="list"/"items"/"distributions" — keep only these keys per row
                (`id` always kept); omitted or `["*"]` = the default view.
        """
        filters = {"visit_type_category_id": visit_type_category_id,
                   "visit_type_id": visit_type_id,
                   "treatment_type_id": treatment_type_id, "search": search}
        if op in ("list", "get"):
            return _serve_kind(_KINDS, kind, op, client=_client, clinic_id=clinic_id,
                               item_id=item_id, filters=filters, limit=limit, offset=offset,
                               fields=fields)
        if op not in ("items", "distributions"):
            raise _bad("op doit être 'list', 'get', 'items' ou 'distributions'.")
        _refuse_ignored(op, clinic_id=clinic_id, **filters)
        _need(op, item_id=item_id)
        if op == "items":
            if kind != "treatment_package":
                raise _bad("op='items' ne vaut que pour kind='treatment_package'.")
            c = _client()
            return _page(_run(lambda: c.list_treatment_package_items(
                item_id, **_paging(limit, offset))), "items", _shape(_PACKAGE_ITEM),
                fields=fields, withheld=None)
        if kind not in _DISTRIBUTIONS:
            raise _bad("op='distributions' ne vaut que pour kind='treatment_pricing' ou "
                       "'treatment_package'.")
        _refuse_ignored(op, limit=limit, offset=offset)
        c = _client()
        return _page(_run(lambda: _DISTRIBUTIONS[kind](c, item_id)), "distributions",
                     _shape(_USER_DISTRIBUTION), fields=fields, withheld=None)
