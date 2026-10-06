"""Nextmotion — a clinic's catalogue: what is booked, sold, and at what price.

Sibling module of `nextmotion.py` (see `Connector.modules`): same key, same client. A
single tool, `nextmotion_catalog`, because all its objects share list/get under a
clinic (ADR 0047); each `kind` carries at most one filter of its own, refused to the others.

The read ops beyond list/get, on the object they detail:
- `items` — a package's treatments (`treatment_package`), paginated;
- `distributions` — the per-user accounting split of a pricing
  (`treatment_pricing`) or of a package, not paginated;
- `post_treatment` — a treatment type's post-treatment configuration (follow-up and
  reminder emails, their questionnaire model, delays): configuration, not patient
  data.

The writes, all `dry_run=True` by default, `data` passed through the input
allowlist (`nextmotion_entrees`): `create` | `update` | `delete` on visit
types, their categories, treatment types (pricings included, as a COMPLETE list), packages
and accounting distributions; `reorder` of visit types and
categories; `add_item` | `set_items` on a package and `update` | `delete` of a package
line (`treatment_package_item`); `set_distributions` of a pricing or a package;
`update_post_treatment` of a treatment type. Variants (`sub_visit_type`) are written
through their visit type, pricings through their treatment type; extracting a package to a
consultation is not served (medical).

Nothing here carries a patient. The allowlist (`nextmotion_socle`) still removes
the attached files and the questionnaires (`bolt_note`, `survey_form`) that a visit
type or a pricing references.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from fastmcp import FastMCP

from .nextmotion_entrees import (_IN_ACCOUNTING_DISTRIBUTION, _IN_CATEGORY, _IN_PACKAGE,
                                 _IN_PACKAGE_ITEM, _IN_PACKAGE_ITEMS, _IN_POST_TREATMENT,
                                 _IN_REORDER, _IN_TREATMENT_TYPE_CREATE,
                                 _IN_TREATMENT_TYPE_UPDATE, _IN_USER_DISTRIBUTION,
                                 _IN_VISIT_TYPE_CREATE, _IN_VISIT_TYPE_UPDATE)
from .nextmotion_garde import (Kind, Write, _bad, _client, _crud, _need, _paging,
                               _refuse_ignored, _run, _serve)
from .nextmotion_socle import (_ACCOUNTING_DISTRIBUTION, _CATEGORY, _GLOBAL_PRODUCT,
                               _PACKAGE, _PACKAGE_ITEM, _POST_TREATMENT_CONFIG, _PRICING,
                               _SUB_VISIT_TYPE, _TREATMENT_TYPE, _USER_DISTRIBUTION,
                               _VISIT_TYPE, _one, _page, _shape)

_package_item = _shape(_PACKAGE_ITEM)
_user_distribution = _shape(_USER_DISTRIBUTION)
_post_treatment = _shape(_POST_TREATMENT_CONFIG)

_DISTRIBUTIONS = {
    "treatment_pricing": lambda c, i: c.list_treatment_pricing_distributions(i),
    "treatment_package": lambda c, i: c.list_treatment_package_distributions(i),
}


def _distributions_now(kind: str):
    """The preview of a `set_distributions`: the split currently in place."""
    def current(c, i, _):
        return {"distributions": _page(_run(lambda: _DISTRIBUTIONS[kind](c, i)),
                                       "distributions", _user_distribution,
                                       withheld=None)["distributions"]}
    return current


_MAX_PAGES_ITEMS = 20  # 2,000 lines: beyond that, the preview says so


def _items_now(c, i, _):
    """The preview of a `set_items`, which REPLACES the whole list: all the lines in
    place, page after page; `items_complet: false` if the cap cut the read."""
    items: list = []
    for page in range(_MAX_PAGES_ITEMS):
        env = _run(lambda: c.list_treatment_package_items(i, limit=100, offset=100 * page))
        items += _page(env, "items", _package_item, withheld=None)["items"]
        if not (isinstance(env, dict) and env.get("next")):
            return {"items": items, "items_complet": True}
    return {"items": items, "items_complet": False}


def _post_treatment_now(c, i, _=None):
    return _one(_run(lambda: c.get_post_treatment_config(i)), "post_treatment_config",
                _post_treatment, withheld=None)


def _set_distributions(call):
    return Write(call, "item", _IN_USER_DISTRIBUTION, ("user", "accounting_distribution"),
                 many=True, key="distributions", shape=_user_distribution, withheld=None)


def _reorder(call):
    return Write(call, "clinic", _IN_REORDER, ("id",), many=True)


_KINDS = {
    "visit_type": Kind(
        "visit_types", _shape(_VISIT_TYPE),
        lambda c, cid, visit_type_category_id, **p: c.list_visit_types(
            cid, visit_type_category_id=visit_type_category_id, **p),
        lambda c, i: c.get_visit_type(i), ("visit_type_category_id",),
        writes={**_crud(lambda c, cid, b: c.create_visit_type(cid, body=b),
                        lambda c, i, b: c.update_visit_type(i, body=b),
                        lambda c, i, b: c.delete_visit_type(i), _IN_VISIT_TYPE_CREATE,
                        ("subject", "color"), accepted_update=_IN_VISIT_TYPE_UPDATE),
                "reorder": _reorder(lambda c, cid, b: c.reorder_visit_types(cid, items=b))}),
    "visit_type_category": Kind(
        "visit_type_categories", _shape(_CATEGORY),
        lambda c, cid, **p: c.list_visit_type_categories(cid, **p),
        lambda c, i: c.get_visit_type_category(i),
        writes={**_crud(lambda c, cid, b: c.create_visit_type_category(cid, body=b),
                        lambda c, i, b: c.update_visit_type_category(i, body=b),
                        lambda c, i, b: c.delete_visit_type_category(i), _IN_CATEGORY,
                        ("name",)),
                "reorder": _reorder(
                    lambda c, cid, b: c.reorder_visit_type_categories(cid, items=b))}),
    "sub_visit_type": Kind(
        "sub_visit_types", _shape(_SUB_VISIT_TYPE),
        lambda c, cid, visit_type_id, **p: c.list_sub_visit_types(
            cid, visit_type_id=visit_type_id, **p),
        lambda c, i: c.get_sub_visit_type(i), ("visit_type_id",)),
    "treatment_type": Kind(
        "treatment_types", _shape(_TREATMENT_TYPE),
        lambda c, cid, search, **p: c.list_treatment_types(cid, search=search, **p),
        lambda c, i: c.get_treatment_type(i), ("search",),
        writes={**_crud(lambda c, cid, b: c.create_treatment_type(cid, body=b),
                        lambda c, i, b: c.update_treatment_type(i, body=b),
                        lambda c, i, b: c.delete_treatment_type(i),
                        _IN_TREATMENT_TYPE_CREATE, ("name",),
                        accepted_update=_IN_TREATMENT_TYPE_UPDATE),
                "update_post_treatment": Write(
                    lambda c, i, b: c.update_post_treatment_config(i, body=b), "item",
                    _IN_POST_TREATMENT, current=_post_treatment_now,
                    key="post_treatment_config", shape=_post_treatment, withheld=None)}),
    "treatment_pricing": Kind(
        "treatment_pricings", _shape(_PRICING),
        lambda c, cid, treatment_type_id, **p: c.list_treatment_pricings(
            cid, treatment_type_id=treatment_type_id, **p),
        lambda c, i: c.get_treatment_pricing(i), ("treatment_type_id",),
        writes={"set_distributions": _set_distributions(
            lambda c, i, b: c.set_treatment_pricing_distributions(i, items=b))}),
    "treatment_package": Kind(
        "treatment_packages", _shape(_PACKAGE),
        lambda c, cid, search, **p: c.list_treatment_packages(cid, search=search, **p),
        lambda c, i: c.get_treatment_package(i), ("search",),
        writes={**_crud(lambda c, cid, b: c.create_treatment_package(cid, body=b),
                        lambda c, i, b: c.update_treatment_package(i, body=b),
                        lambda c, i, b: c.delete_treatment_package(i), _IN_PACKAGE,
                        ("name",)),
                "add_item": Write(
                    lambda c, i, b: c.create_treatment_package_item(i, body=b), "item",
                    _IN_PACKAGE_ITEM, ("pricing",), key="item", shape=_package_item,
                    withheld=None),
                "set_items": Write(
                    lambda c, i, b: c.replace_treatment_package_items(i, items=b), "item",
                    _IN_PACKAGE_ITEMS, ("pricing",), many=True, current=_items_now,
                    key="items", shape=_package_item, withheld=None),
                "set_distributions": _set_distributions(
                    lambda c, i, b: c.set_treatment_package_distributions(i, items=b))}),
    "treatment_package_item": Kind(
        "items", _package_item,
        writes={"update": Write(lambda c, i, b: c.update_treatment_package_item(i, body=b),
                                "item", _IN_PACKAGE_ITEM, ("pricing",)),
                "delete": Write(lambda c, i, b: c.delete_treatment_package_item(i))}),
    "accounting_distribution": Kind(
        "accounting_distributions", _shape(_ACCOUNTING_DISTRIBUTION),
        lambda c, cid, **p: c.list_accounting_distributions(cid, **p),
        lambda c, i: c.get_accounting_distribution(i),
        writes=_crud(lambda c, cid, b: c.create_accounting_distribution(cid, body=b),
                     lambda c, i, b: c.update_accounting_distribution(i, body=b),
                     lambda c, i, b: c.delete_accounting_distribution(i),
                     _IN_ACCOUNTING_DISTRIBUTION, ("name", "model"))),
    "global_product": Kind(
        "global_products", _shape(_GLOBAL_PRODUCT),
        lambda c, cid, search, **p: c.list_global_products(cid, search=search, **p),
        None, ("search",)),
}

_READS = ("items", "distributions", "post_treatment")


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_catalog(
        kind: Literal["visit_type", "visit_type_category", "sub_visit_type",
                      "treatment_type", "treatment_pricing", "treatment_package",
                      "treatment_package_item", "accounting_distribution",
                      "global_product"],
        op: Literal["list", "get", "items", "distributions", "post_treatment", "create",
                    "update", "delete", "reorder", "add_item", "set_items",
                    "set_distributions", "update_post_treatment"] = "list",
        clinic_id: Optional[str] = None,
        item_id: Optional[str] = None,
        visit_type_category_id: Optional[str] = None,
        visit_type_id: Optional[str] = None,
        treatment_type_id: Optional[str] = None,
        search: Optional[str] = None,
        data: Optional[Union[dict, list]] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """The service catalogue of a Nextmotion clinic — what is booked or sold, at
        what price, how the price is split — read and write.

        `kind`: "visit_type" (filter `visit_type_category_id`) | "visit_type_category" |
        "sub_visit_type" (filter `visit_type_id`; written through its visit type) |
        "treatment_type" (filter `search`) | "treatment_pricing" (filter
        `treatment_type_id`; written through its treatment type) | "treatment_package"
        (filter `search`) | "treatment_package_item" (update | delete only) |
        "accounting_distribution" | "global_product" (filter `search`; list only).

        `op` reads: "list" (default, `clinic_id`) | "get" (`item_id`) | "items"
        (package lines) | "distributions" (per-user split of a pricing or package) |
        "post_treatment" (a treatment type's follow-up emails config).
        `op` writes: "create" (`clinic_id`) | "update" | "delete" (`item_id`);
        "reorder" (visit_type / visit_type_category, `clinic_id`, `data` =
        `[{"id"}, …]`); "add_item" | "set_items" (package); "set_distributions"
        (pricing / package, `[{user, accounting_distribution}]`);
        "update_post_treatment" (treatment_type).
        ⚠️ These lists REPLACE everything, an omitted element is deleted:
        `sub_visit_types`, `pricings`, set_items, set_distributions.
        `data` takes the fields the Nextmotion spec accepts for the op; any other field is refused.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act.

        Args:
            kind: which catalogue.
            op: see above (default "list").
            clinic_id: op="list"/"create"/"reorder".
            item_id: every other op — the item of that kind.
            visit_type_category_id / visit_type_id / treatment_type_id / search: list
                filters (see above).
            data: writes — an object, or a list for reorder / set_items / set_distributions.
            dry_run: writes — default True.
            limit / offset: list/items — pagination (limit 1..100, default 50).
            fields: list/items/distributions — keep only these keys per row (`id` kept)."""
        filters = {"visit_type_category_id": visit_type_category_id,
                   "visit_type_id": visit_type_id,
                   "treatment_type_id": treatment_type_id, "search": search}
        if op not in _READS:
            if kind == "treatment_package_item" and op in ("list", "get"):
                raise _bad("kind='treatment_package_item' is read through op='items' on "
                           "kind='treatment_package'.")
            return _serve(_KINDS, kind, op, client=_client, clinic_id=clinic_id,
                          item_id=item_id, filters=filters, limit=limit, offset=offset,
                          fields=fields, data=data, dry_run=dry_run)
        _refuse_ignored(op, clinic_id=clinic_id, data=data, dry_run=dry_run, **filters)
        _need(op, item_id=item_id)
        if op == "items":
            if kind != "treatment_package":
                raise _bad("op='items' only applies to kind='treatment_package'.")
            c = _client()
            return _page(_run(lambda: c.list_treatment_package_items(
                item_id, **_paging(limit, offset))), "items", _package_item,
                fields=fields, withheld=None)
        _refuse_ignored(op, limit=limit, offset=offset)
        if op == "post_treatment":
            if kind != "treatment_type":
                raise _bad("op='post_treatment' only applies to kind='treatment_type'.")
            _refuse_ignored(op, fields=fields)
            return _post_treatment_now(_client(), item_id)
        if kind not in _DISTRIBUTIONS:
            raise _bad("op='distributions' only applies to kind='treatment_pricing' or "
                       "'treatment_package'.")
        c = _client()
        return _page(_run(lambda: _DISTRIBUTIONS[kind](c, item_id)), "distributions",
                     _user_distribution, fields=fields, withheld=None)
