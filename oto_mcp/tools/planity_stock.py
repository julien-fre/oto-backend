"""Planity — a salon's STOCK: movements, suppliers, orders, removals.

Sibling module of `planity.py` (the product catalogue and its lots live there, on
`planity_list_products`): same connector, same namespace, same session. Here, what
MOVES — every entry and every exit, with its date, type and purchase
price.

There is **no order forecast tool**, and that is deliberate: the rule
(how many days of coverage, what supplier lead time, which families to
restock) belongs to the salon, not to the connector. A forecast coded here would be
ours, frozen, and wrong at the next one. The tools return the facts; the agent
composes — the recipe takes three calls, it is in the connector's listing.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .planity_session import _bad, _client, _eur_ou_rien, fenetre, iso, periode

#: The refusal opposed to an implicit sweep of the catalogue, and the two paths
#: that cost one call instead of six hundred.
#:
#: ⚠️ **It falls BEFORE any read, and that is the point.** It was first written
#: after a `list_products` that served to count the products — so a condemned
#: call still paid for a read, on a session the caller believed untouched.
#: Counting in order to refuse is already having done what we refuse.
_REFUS_BALAYAGE = (
    "Stock movements are read ONE PRODUCT AT A TIME — there is no "
    "time index at salon level. Sweeping a whole catalogue would hold up the "
    "conversation for tens of seconds, so this tool does not do it on its "
    "own: pass `product_ids`. The identifiers come from "
    "`planity_list_products` (one call). And for a salon-wide view, "
    "`planity_get_revenue_breakdown` gives the quantities sold per product in ONE "
    "call — that is where to start, then come here for the detail of the few "
    "products that stand out.")


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def planity_list_stock_movements(
        salon_id: str,
        product_ids: Optional[list[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Raw stock movements in a date range: every in and out, per product.

        Returns {period, products_scanned, count, movements: [{product_id, id,
        date, type, quantity, purchase_price_eur, motive}]}, oldest first.

        `type` is Planity's own: `creation` (stock created), `sale`, `update`
        (manual correction), `saleCancellation` (a sale given back). Types outside
        that list are returned as-is rather than dropped.

        `purchase_price_eur` is null when the movement carries no amount — Planity
        also stores the string `"any"` there, for a movement that targets no
        particular purchase lot. `purchase_price_raw` keeps what was actually
        written. Null means "not an amount", never "free".

        `product_ids` is REQUIRED in practice: movements are read one product at a
        time, so a whole-catalogue sweep is dozens of seconds and this tool will not
        do one implicitly. Without it, the call is refused immediately — **before
        any read** — naming the two cheaper paths: `planity_list_products` for the
        ids, and `planity_get_revenue_breakdown` for a salon-wide view in one call.
        """
        ids = [i for i in (product_ids or []) if i]
        if not ids:
            # BEFORE `_client()`: a condemned call does not even open a session.
            raise _bad(_REFUS_BALAYAGE)
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        mouvements = await c.list_stock_movements(salon_id, ids, gte, lte)
        return {
            "period": periode(gte, lte),
            "products_scanned": len(ids),
            "count": len(mouvements),
            "movements": [
                {"product_id": m["product_id"], "id": m["id"],
                 "date": iso(m["created_at"]), "type": m["type"],
                 "quantity": m["quantity"],
                 "purchase_price_eur": _eur_ou_rien(m["purchase_price_cents"]),
                 "purchase_price_raw": m["purchase_price_raw"],
                 "motive": m["motive"]}
                for m in mouvements
            ],
        }

    @mcp.tool()
    async def planity_list_suppliers(salon_id: str) -> dict:
        """Suppliers declared for the salon's shop.

        Returns {count, suppliers}. An empty list is a normal answer: a salon that
        orders by phone or email declares none, and that is not an error to retry.
        """
        fournisseurs = await (await _client()).list_suppliers(salon_id)
        return {"count": len(fournisseurs), "suppliers": fournisseurs}

    @mcp.tool()
    async def planity_list_product_orders(salon_id: str,
                                          cursor: Optional[str] = None) -> dict:
        """One page of product (restocking) orders. Returns {count, orders, cursor}.

        Pass the returned `cursor` back to get the next page; a `cursor` of null
        means there is no next page. An empty list is a normal answer for a salon
        that does not order through Planity.
        """
        page = await (await _client()).list_product_orders(salon_id, cursor=cursor)
        return {"count": len(page["data"]), "orders": page["data"],
                "cursor": page["cursor"]}

    @mcp.tool()
    async def planity_list_mass_stock_removals(salon_id: str,
                                               limit: int = 20) -> dict:
        """Bulk stock write-offs (inventory correction, breakage, expiry), newest last.

        Returns {count, removals: [{id, date, products_count, products}]}. These do
        NOT appear as `sale` movements: a stock that dropped without a sale is
        usually one of these, and looking only at sales makes the difference
        unexplainable.
        """
        sorties = await (await _client()).list_mass_stock_removals(salon_id, limit)
        return {
            "count": len(sorties),
            "removals": [{"id": s["id"], "date": iso(s["created_at"]),
                          "products_count": s["products_count"],
                          "products": s["products"]}
                         for s in sorties],
        }
