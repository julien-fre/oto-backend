"""Planity — a salon's FIGURES: till, staff, occupancy, reviews.

Sibling module of `planity.py` (reference data, customers, calendar): same
connector, same `planity_*` namespace, same credential, same session
(`planity_session`). Split because the connector's twenty tools don't fit in one
file, and the dividing line that makes sense follows the two families of sources —
the figures on one side, the reference data and the calendar on the other.

⚠️ **`planity_get_revenue_breakdown` can return an EMPTY `by_seller`**, and that
is NOT a salon without sales: Planity doesn't fill in that breakdown. The
per-staff breakdown that is authoritative is `planity_get_seller_stats` —
that's the one to read, and that's what to answer to anyone who is surprised.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .planity_session import _client, _eur, _eur_ou_rien, fenetre, iso, periode


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def planity_get_revenue_summary(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Revenue KPIs for a period: total CA (TTC/HT), ticket count, VAT, average basket.

        Default: last 7 days. Use preset for quick ranges ("today", "week", "month", "30d"...).

        ⚠️ Read `period` before projecting anything from these figures. Most presets
        stop at NOW, not at the end of the day: `period.ends_today` true means the
        last day is partial and `period.complete` is false, so a daily run rate
        computed from `revenue_ttc_eur / period.days` comes out too low — and a
        "at this pace, N days left" built on it comes out wrong.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        ki = await c.get_key_indicators(salon_id, gte, lte)
        return {
            "period": periode(gte, lte),
            "from": iso(gte), "to": iso(lte),
            "revenue_ttc_eur": _eur(ki.get("revenueWithVAT")),
            "revenue_ht_eur": _eur(ki.get("revenueWithoutVAT")),
            "ticket_count": ki.get("amountOfReceipts", 0),
            "vat_eur": _eur(ki.get("VATValue")),
            "average_basket_eur": _eur(ki.get("averageBasket")),
        }

    @mcp.tool()
    async def planity_get_daily_revenue(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Daily revenue: {period, days: [{date, revenue_ttc_eur, revenue_ht_eur, quantity}]}.

        Useful for trend analysis or plotting.

        ⚠️ `period` says what the series covers, and whether its LAST day is still
        running (`ends_today` / `complete`). A day in progress is not a low day —
        averaging it in, or extrapolating from it, is how a projection goes wrong
        with nothing to show for it. Days with no takings are absent from the
        series rather than present at zero: `len(days)` is not `period.days`.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        data = await c.get_revenues(salon_id, gte, lte)
        jours = []
        for ts_str, bucket in sorted((data.get("all") or {}).items()):
            jour = iso(int(ts_str))
            jours.append({
                "date": (jour or "")[:10],
                "timestamp": jour,
                "revenue_ttc_eur": _eur(bucket.get("revenueWithVAT")),
                "revenue_ht_eur": _eur(bucket.get("revenueWithoutVAT")),
                "quantity": bucket.get("quantity", 0),
            })
        return {"period": periode(gte, lte), "days_with_revenue": len(jours),
                "days": jours}

    @mcp.tool()
    async def planity_get_best_customers(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Top-spending customers for a period (Planity's 'best customers' analysis)."""
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        return await c.get_best_customers(salon_id, gte, lte)

    @mcp.tool()
    async def planity_get_new_customers(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Customers acquired for the first time during the period."""
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        return await c.get_new_customers(salon_id, gte, lte)

    @mcp.tool()
    async def planity_get_customer_frequencies(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Overall customer visit frequency distribution (who comes how often)."""
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        return await c.get_overall_frequencies(salon_id, gte, lte)

    @mcp.tool()
    async def planity_get_revenue_breakdown(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Multi-dimensional revenue breakdown: total CA + splits by service, product,
        seller (employee), other line types, and gift vouchers, in a single call.

        All amounts in euros. Use this to answer "how is my revenue broken down?".
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        raw = await c.get_revenue_breakdown(salon_id, gte, lte)
        totals = raw.get("totals") or {}

        def _block(key: str) -> dict:
            b = raw.get(key) or {}
            t = b.get("totals") or {}
            data = b.get("data") or {}
            items = []
            if isinstance(data, dict):
                for cat_key, cat in data.items():
                    if not isinstance(cat, dict):
                        continue
                    for child in cat.get("children") or []:
                        if not isinstance(child, dict):
                            continue
                        items.append({
                            "name": child.get("name"),
                            "revenue_eur": _eur(child.get("revenue")),
                            "quantity": child.get("amount"),
                            "discount_eur": _eur(child.get("discount")),
                            "margin_eur": _eur(child.get("margin")),
                            "category_id": cat_key,
                        })
            items.sort(key=lambda i: i["revenue_eur"], reverse=True)
            return {
                "total_eur": _eur(t.get("totalRevenue")),
                "quantity": t.get("totalAmount", 0),
                "items": items,
            }

        # bySeller may be list or dict
        by_seller_raw = raw.get("bySeller") or []
        sellers_out = []
        if isinstance(by_seller_raw, list):
            for row in by_seller_raw:
                if isinstance(row, dict):
                    sellers_out.append({
                        "seller_id": row.get("sellerId"),
                        "revenue_eur": _eur(row.get("revenue")),
                        "quantity": row.get("amount"),
                    })

        return {
            "from": iso(gte), "to": iso(lte),
            "grand_total_eur": _eur(totals.get("totalRevenue")),
            "grand_total_ht_eur": _eur(totals.get("totalPriceVATExcluded")),
            "total_discount_eur": _eur(totals.get("totalDiscount")),
            "ticket_count": totals.get("totalAmount", 0),
            "profitability_rate": totals.get("profitabilityRate"),
            "by_service": _block("byService"),
            "by_product": _block("byProduct"),
            "by_other": _block("byOther"),
            "by_gift_voucher": _block("byGiftVoucherAtSale"),
            "by_seller": sellers_out,
        }

    @mcp.tool()
    async def planity_get_seller_stats(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> list[dict]:
        """Per-employee (seller) stats for a period.

        Returns revenue, average basket, and appointment count for each employee.
        Use this to compare one employee against the rest of the team.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        raw = await c.get_calendar_stats(salon_id, gte, lte)
        salon = await c.get_salon(salon_id)
        name_by_id = {e.id: e.name for e in salon.employees}

        # The response has two shapes:
        #   - data: list of tuples [sellerId, ?, ?, ?, totalRevenue_cents, avgBasket, ?, ?]
        #   - bySeller.data: dict keyed by sellerId, with
        #     {onlineAppointments, offlineAppointments, ...}
        tuples = raw.get("data") or []
        by_seller = (raw.get("bySeller") or {}).get("data") or {}
        out = []
        for row in tuples:
            if not isinstance(row, list) or len(row) < 6:
                continue
            sid = row[0]
            out.append({
                "seller_id": sid,
                "name": name_by_id.get(sid, "?"),
                "revenue_eur": _eur(row[4]),
                "average_basket_eur": (round(float(row[5]), 2)
                                       if isinstance(row[5], (int, float)) else None),
                "appointments_done": row[1],
                "services_count": row[2],
                "appointments": (by_seller.get(sid) or {}),
            })
        out.sort(key=lambda s: s["revenue_eur"], reverse=True)
        return out

    @mcp.tool()
    async def planity_get_occupancy_rate(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Occupancy heatmap: matrix of (day of week × time slot) with rates 0..1+.

        1.0 = fully booked. >1.0 = double-booked. Useful for spotting the busiest
        hours and idle slots.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        raw = await c.get_occupancy_rate(salon_id, gte, lte)
        return {
            "from": iso(gte), "to": iso(lte),
            "matrix": raw.get("matrix"),
            "raw": raw,
        }

    @mcp.tool()
    async def planity_get_reviews_stats(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Reviews ratings aggregated by calendar and by service (Planity reviews).

        Useful for "which services are rated best".
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        return await c.get_reviews_stats(salon_id, gte, lte)

    # ═══════════════════════ Caisse — ventilations ═══════════════════════

    @mcp.tool()
    async def planity_get_revenue_by_payment_method(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Revenue split by payment method (card, cash, voucher…), total and per day.

        Answers "how much by card, how much in cash". Method names are Planity's
        own; `planity_list_payment_methods` gives the salon's table of them.

        `revenue_eur` is money. `amount` is passed through UNCONVERTED and
        uninterpreted: it is not the same magnitude as the revenue, and what it
        counts has not been established — do not read it as euros.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        brut = await c.get_revenue_by_payment_method(salon_id, gte, lte)
        totaux: dict = {}
        jours = []
        for ts, methodes in sorted((brut or {}).items()):
            if not isinstance(methodes, dict):
                continue
            ligne = {"date": (iso(int(ts)) or "")[:10], "methods": []}
            for cle, m in methodes.items():
                if not isinstance(m, dict):
                    continue
                nom = m.get("paymentMethodName") or cle
                revenu = m.get("revenue")
                ligne["methods"].append({
                    "method": cle, "method_name": nom,
                    "revenue_eur": _eur_ou_rien(revenu), "amount": m.get("amount")})
                agg = totaux.setdefault(cle, {"method": cle, "method_name": nom,
                                              "revenue_cents": 0})
                agg["revenue_cents"] += revenu or 0
            jours.append(ligne)
        return {
            "period": periode(gte, lte),
            "by_method": sorted(
                ({"method": t["method"], "method_name": t["method_name"],
                  "revenue_eur": _eur(t["revenue_cents"])} for t in totaux.values()),
                key=lambda t: -t["revenue_eur"]),
            "by_day": jours,
        }

    @mcp.tool()
    async def planity_get_revenue_by_vat(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Revenue split by VAT rate, as Planity aggregates it.

        Two views come back: `by_vat_id` (per rate, with its day series and totals)
        and `by_period`. They are passed through with their own shape and their own
        units — this tool does not reshape or convert them, because nothing here
        establishes what each figure means, and a euro sign put on the wrong one
        reads as a filed VAT amount.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        brut = await c.get_revenue_by_vat(salon_id, gte, lte) or {}
        return {"period": periode(gte, lte),
                "by_vat_id": brut.get("byVatId"), "by_period": brut.get("byPeriod")}

    @mcp.tool()
    async def planity_get_service_stats(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
    ) -> dict:
        """Per-service volume and revenue stats for a period.

        `by_service` is keyed by service id — resolve the names with
        `planity_list_services` (pass `include_deleted=true` for a service that has
        since been removed from the catalogue). `rows` is Planity's own table, one
        row per service, first column the service id; it carries ids and numbers
        only, no names.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        brut = await c.get_service_stats(salon_id, gte, lte) or {}
        return {"period": periode(gte, lte),
                "by_service": brut.get("byService"), "rows": brut.get("data")}
