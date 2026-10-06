"""Planity — a salon's reference data, customers and calendar (READ-ONLY).

The connector authenticates with the email and password of the person's Planity
account, stored in the vault (`byo_user`, `secret_kind="basic_auth"`). The client
lives in oto-core (`oto.tools.planity`); here there are only thin wrappers:
resolve the credential, call the client, return clean JSON.

The statistics (revenue, staff, occupancy, reviews) are in the sibling module
`planity_stats.py` — same connector, same namespace, same key.

⚠️ **This connector authenticates with the Planity account's email and password,
and nothing else** — it takes no other authentication path of the Planity
application. What it can read is therefore exactly what that account can read: the
scope is set by choosing the account, not by configuring oto. The connector sheet
(`connectors/docs/planity.md`) tells whoever sets the credential.

Edge conventions, inherited from the original server and unchanged (agents and the
sheet know these names and schemas):
- prices are in cents at Planity, returned in euros;
- timestamps are in milliseconds, returned as ISO (Europe/Paris);
- time-based tools accept `date_from`/`date_to` (ISO or preset), default 7 days.

⚠️ **WHITELIST on customer data — a deliberate exception to "expose the raw data,
the agent decides".** The connector's stance is to return what upstream gives and
let the agent compose. That stops at a THIRD PARTY's personal data: a salon's
customer is neither the tool's user nor the user's own customer, she asked for
nothing, and her name, phone, email, address or the comment written about her
have no business crossing a transcript to answer "how many appointments on
Thursday".

So: every tool that touches an appointment, a ticket, a review or a record returns
a whitelist of NAMED fields, never the full object — and for the customer, an
**identifier only**. The tools that serve a customer by name
(`planity_get_customer`, `planity_search_customers`) are the exception: that is
their purpose, the caller asked for them, and the agent composes from the
identifier.

This is not an oversight to fix in the name of the stance: it is the stance,
bounded where it would cost someone who is not in the conversation. The oto-core
core, for its part, returns the raw data — it is a library; the boundary is HERE.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastmcp import FastMCP

from ..connectors import verify as connector_verify
from . import planity_session
from .planity_session import _client, _eur, _eur_ou_rien, fenetre, iso


def _employe(e) -> dict:
    """A calendar child as it goes out — deletion and kind included."""
    return {"id": e.id, "name": e.name, "type": e.type, "title": e.title,
            "color": e.color, "calendar_id": e.calendar_id,
            "deleted": e.deleted, "deleted_at": iso(e.deleted_at)}


def _rdv_public(v: dict) -> dict:
    """An appointment reduced to the fields that go out — the WHITELIST.

    It is written here, once, rather than in each tool: what protects a customer
    must not depend on who copies what. What isn't in it isn't forgotten, it is
    REFUSED — the customer's name, phone, email, the free-text comment (it carries
    names), and the raw object.

    The comment and the title are added BY `planity_get_appointment`, which is
    called for one specific appointment: then it is the note we came to fetch, not
    a field slipping through in a list of a hundred."""
    return {
        "id": v["id"],
        "employee_id": v["child_id"],
        "date": v.get("date"),
        "start": v.get("start"),
        "end": v.get("end"),
        "duration_minutes": v.get("duration_minutes"),
        "customer_id": v.get("customer_id"),
        "service_id": v.get("service_id"),
        "price_eur": _eur_ou_rien(v.get("price_cents")),
        "booked_via": v.get("booked_via"),
        "cancelled": v.get("cancelled"),
        "cancelled_at": iso(v.get("cancelled_at")),
        "receipt_id": (v.get("receipt") or {}).get("id"),
        "period_id": (v.get("receipt") or {}).get("period_id"),
        "created_at": iso(v.get("created_at")),
        "updated_at": iso(v.get("updated_at")),
    }


async def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe. Covers `auth` ALONE.

    Plays the full auth chain THEN `list_salons` — and both count. The auth chain
    proves that the email and password pass; `list_salons` proves that the
    enriched token does carry salons AND that the Realtime Database WebSocket
    responds. A Planity account whose token opens no salon authenticates perfectly
    and can read nothing: returning "connected" on that would be the hollow green
    this probe exists to prevent.

    No side effects: three authentication POSTs and some reads. Planity exposes no
    credit counter and no documented quota — hence `auth`, never `auth+quota`: we
    measured no balance.
    """
    # The instance's coordinates BEFORE anything: probing without them would
    # produce a Firebase 400, which we would read as "wrong password" — and we
    # would have a perfectly good credential re-entered.
    coordonnees = await asyncio.to_thread(planity_session.endpoints)
    client = planity_session._coeur().PlanityClient(
        fields["email"], fields["password"], coordonnees)
    try:
        try:
            await client.auth.get_tokens()
        except Exception as e:
            statut = getattr(getattr(e, "response", None), "status_code", None)
            if statut in (400, 401, 403):
                raise connector_verify.NonAutorise(
                    "Planity refused this email or password.") from e
            raise
        salons = await client.list_salons()
        if not salons:
            raise connector_verify.NonAutorise(
                "The account authenticates but opens no salon: its token "
                "carries no establishment. Check that it is a "
                "`pro.planity.com` account attached to a salon.")
    finally:
        await client.close()


def register(mcp: FastMCP) -> None:
    # ⚠️ NO import of the core here. The connector stays MOUNTED even when
    # oto-core's `planity` extra is missing or the coordinates are not set: it is
    # then visible, selectable, and every call refuses by naming what is missing.
    # A connector that vanishes from the catalog goes unnoticed and can't be
    # explained — the user pays the difference.
    planity_session.avertir_au_demarrage()
    connector_verify.register("planity", _verify)

    # ═══════════════════════ Reference data ═══════════════════════

    @mcp.tool()
    async def planity_list_salons() -> list[dict]:
        """List salons accessible to the user.

        Returns {id, name, slug, phone, opening_hours, employee_count, calendar_count}.
        Use `id` as `salon_id` in all other tools.
        """
        c = await _client()
        salons = await c.list_salons()
        return [
            {
                "id": s.id, "name": s.name, "slug": s.slug,
                "phone": s.phone, "opening_hours": s.opening_hours,
                "employee_count": len(s.employees),
                "calendar_count": len(s.calendars),
            }
            for s in salons
        ]

    @mcp.tool()
    async def planity_get_salon_info(salon_id: str) -> dict:
        """Full salon metadata: contact info, opening hours, team, calendars.

        `employees` lists the ACTIVE calendar children; `employees_deleted_count`
        says how many more exist and are gone. Use `planity_list_employees` with
        `include_deleted=true` to see them.
        """
        c = await _client()
        s = await c.get_salon(salon_id)
        return {
            "id": s.id, "name": s.name, "slug": s.slug, "phone": s.phone,
            "opening_hours": s.opening_hours, "db_shard": s.db_shard,
            "calendars": s.calendars,
            "employees": [_employe(e) for e in s.employees if not e.deleted],
            "employees_deleted_count": sum(1 for e in s.employees if e.deleted),
        }

    @mcp.tool()
    async def planity_list_employees(salon_id: str,
                                     include_deleted: bool = False) -> list[dict]:
        """Calendar children of a salon: {id, name, type, title, color, calendar_id,
        deleted, deleted_at}.

        Deleted children are EXCLUDED by default. Planity keeps them — their past
        appointments live in their calendar — so counting them as staff announces a
        team that has not existed for months. Pass `include_deleted=true` when you
        are reading history, not staffing.

        Not every child is a person: `type` and `title` are returned as Planity
        stores them, so a room, a chair or any bookable resource can be told apart
        from a colleague. They are passed through, not interpreted.
        """
        c = await _client()
        s = await c.get_salon(salon_id)
        return [_employe(e) for e in s.employees if include_deleted or not e.deleted]

    @mcp.tool()
    async def planity_list_services(salon_id: str,
                                    include_deleted: bool = False) -> list[dict]:
        """Bookable services catalog (flattened children).

        Planity groups services under categories; each leaf child is the actual
        bookable service. Returns {id, category_id, name, duration_minutes,
        bookable, description, price: {kind, ...}}.

        A service does NOT have one price. `price.kind` says which of four
        situations you are in — `fixed`, `range` (min/max), `on_quotation`, or
        `unpriced` (no price set at all, which is roughly half the catalogue).
        Euros AND raw cents are both returned: a range has no single euro figure to
        average, and rounding it would invent one.

        Deleted services are EXCLUDED by default, and so are the live services of a
        deleted CATEGORY — neither can be booked. Pass `include_deleted=true` to
        resolve an old `service_id` found on a past appointment or receipt.
        """
        c = await _client()
        coeur = planity_session._coeur()
        out = []
        for s in coeur.services.aplatir(await c.list_services(salon_id)):
            if s["deleted"] and not include_deleted:
                continue
            prix = s.pop("prices")
            s["deleted_at"] = iso(s["deleted_at"])
            s["category_deleted_at"] = iso(s["category_deleted_at"])
            out.append({
                **s,
                "price": {
                    "kind": prix["kind"],
                    "default_eur": _eur_ou_rien(prix["default_cents"]),
                    "min_eur": _eur_ou_rien(prix["min_cents"]),
                    "max_eur": _eur_ou_rien(prix["max_cents"]),
                    "default_cents": prix["default_cents"],
                    "min_cents": prix["min_cents"],
                    "max_cents": prix["max_cents"],
                },
            })
        return out

    @mcp.tool()
    async def planity_list_products(salon_id: str, in_stock_only: bool = False,
                                    include_deleted: bool = False) -> list[dict]:
        """Product catalog (shop items sold in the salon), with stock and reorder data.

        Returns {id, category_id, name, price_eur, ean, brand, stock_total,
        stock_lots, stock_threshold, stock_ceiling, supplier_id, deleted,
        deleted_at}.

        `stock_lots` is the point: stock is not a number but a list of purchase
        lots, each with its own `purchase_price_eur`. Flatten it and the margin
        disappears with it.

        `stock_threshold` / `stock_ceiling` / `supplier_id` are `null` when the
        salon does not use them — `null` is NOT `0`. A reorder rule that reads a
        missing threshold as zero orders everything, every time.
        """
        c = await _client()
        coeur = planity_session._coeur()
        out = []
        for p in coeur.stock.aplatir_produits(await c.list_products(salon_id)):
            if p["deleted"] and not include_deleted:
                continue
            if in_stock_only and p["stock_total"] <= 0:
                continue
            out.append({
                "id": p["id"], "category_id": p["category_id"], "name": p["name"],
                "price_eur": _eur(p["price_cents"]), "ean": p["ean"],
                "brand": p["brand"],
                "stock_total": p["stock_total"],
                "stock_lots": [
                    {"quantity": l["quantity"],
                     "purchase_price_eur": _eur_ou_rien(l["purchase_price_cents"]),
                     "created_at": iso(l["created_at"])}
                    for l in p["stock_lots"]
                ],
                "stock_threshold": p["stock_threshold"],
                "stock_ceiling": p["stock_ceiling"],
                "supplier_id": p["supplier_id"],
                "deleted": p["deleted"], "deleted_at": iso(p["deleted_at"]),
            })
        return out

    # ═══════════════════════ Clientes ═══════════════════════

    @mcp.tool()
    async def planity_search_customers(salon_id: str, query: str = "",
                                       limit: int = 10) -> list[dict]:
        """Search customers by name/phone/email within a salon (Algolia).

        Empty query returns most recent customers.
        """
        c = await _client()
        hits = await c.search_customers(salon_id, query=query, limit=limit)
        return [
            {
                "id": h.get("id") or h.get("objectID"),
                "name": (h.get("name") or "").strip(),
                "phone": h.get("phone"),
                "email": h.get("email"),
                "gender": h.get("gender"),
                "created_at": iso(h.get("createdAt")),
            }
            for h in hits
        ]

    @mcp.tool()
    async def planity_get_customer(salon_id: str, customer_id: str) -> dict:
        """Full customer profile (name, phone, address, note, vevent IDs).

        Does NOT include stats/receipts — use planity_get_customer_stats /
        planity_get_customer_receipts for those.
        """
        c = await _client()
        p = await c.get_customer(salon_id, customer_id)
        vevents = p.get("vevents") or {}
        return {
            "id": customer_id,
            "name": (p.get("name") or "").strip(),
            "phone": p.get("phone"),
            "email": p.get("email"),
            "address": p.get("address"),
            "postal_code": p.get("postalCode"),
            "city": p.get("city"),
            "gender": p.get("gender"),
            "comment": p.get("comment"),
            "created_at": iso(p.get("createdAt")),
            "skip_marketing_sms": p.get("skipMarketingSMS"),
            "vevent_ids": list(vevents.keys()) if isinstance(vevents, dict) else [],
        }

    @mcp.tool()
    async def planity_get_customer_stats(salon_id: str, customer_id: str) -> dict:
        """Appointments count + revenue stats (service/product breakdown) for a customer.

        Average basket, revenue in euros, appointment frequency in days.
        """
        c = await _client()
        raw = await c.get_customer_stats(salon_id, customer_id)
        rev = raw.get("revenue") or {}
        appts = raw.get("appointments") or {}
        return {
            "appointments": {
                "total": appts.get("total", 0),
                "by_web": appts.get("byWeb", 0),
                "by_pro": appts.get("byPro", 0),
                "frequency_days": appts.get("frequency", 0),
            },
            "revenue": {
                "total_eur": _eur(rev.get("total")),
                "average_basket_eur": _eur(rev.get("average")),
                "by_service_eur": _eur(rev.get("totalByService")),
                "by_product_eur": _eur(rev.get("totalByProduct")),
                "service_share_pct": round(rev.get("rateByService", 0), 1),
                "product_share_pct": round(rev.get("rateByProduct", 0), 1),
            },
        }

    @mcp.tool()
    async def planity_get_customer_receipts(salon_id: str, customer_id: str,
                                            limit: int = 10) -> list[dict]:
        """Receipt history (tickets) for a customer, most recent first.

        Each ticket has total_eur + lines (services/products mix).
        """
        c = await _client()
        raw = await c.get_customer_receipts(salon_id, customer_id)
        # Sort by createdAt desc
        raw_sorted = sorted(raw, key=lambda r: r.get("createdAt", 0), reverse=True)
        out = []
        for r in raw_sorted[:limit]:
            lines = r.get("lines") or []
            out.append({
                "id": r.get("receiptId"),
                "date": iso(r.get("createdAt")),
                "total_eur": _eur(sum(l.get("price", 0) for l in lines)),
                "lines": [
                    {
                        "price_eur": _eur(l.get("price")),
                        "service_id": l.get("serviceId"),
                        "product_id": l.get("productId"),
                        "cure_id": l.get("cureId"),
                        "gift_voucher_id": l.get("giftVoucherId"),
                    }
                    for l in lines
                ],
            })
        return out

    # ═══════════════════════ Agenda ═══════════════════════

    @mcp.tool()
    async def planity_list_appointments(
        salon_id: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        preset: Optional[str] = None,
        employee_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict:
        """List appointments in a date range, optionally for one employee.

        Accepts presets: "today", "this_week", "7d", "30d"... Default: last 7 days.
        Reads every employee calendar of the salon unless `employee_id` narrows it.

        Times are the salon's WALL CLOCK, as Planity stores them — no UTC offset is
        added, because there is none to add and inventing one would be wrong half
        the year.

        A cancelled appointment is returned like any other, with `cancelled: true`
        — Planity has no status field, only a deletion date, and hiding them would
        hide cancellations from whoever is looking for them.

        ⚠️ Returns an allow-list of fields, and for the customer an **id only** —
        no name, no phone, no email, and not the free-text comment (it routinely
        contains people's names). Use `planity_get_appointment` for the comment of
        one appointment, and `planity_get_customer` to resolve an id. This is a
        deliberate exception to "expose the raw" — see the module docstring — not
        an omission to fix.
        """
        c = await _client()
        gte, lte = fenetre(date_from, date_to, preset)
        rdv = await c.list_appointments(salon_id, iso(gte)[:10], iso(lte)[:10],
                                        employee_id=employee_id)
        return {
            "count": len(rdv), "from": iso(gte)[:10], "to": iso(lte)[:10],
            "truncated": len(rdv) > limit,
            "appointments": [_rdv_public(v) for v in rdv[:limit]],
        }

    @mcp.tool()
    async def planity_get_appointment(salon_id: str, vevent_id: str,
                                      employee_id: Optional[str] = None) -> dict:
        """One appointment in full, including its free-text comment.

        Without `employee_id`, every calendar of the salon is searched: an
        appointment id does not say which calendar it belongs to.

        ⚠️ Same allow-list as the listing, plus `comment` and `title` — this tool
        is asked for ONE appointment, so its note is what was asked for. The
        customer is still an **id only**; resolve it with `planity_get_customer`.
        """
        c = await _client()
        v = await c.get_appointment(salon_id, vevent_id, employee_id=employee_id)
        if v is None:
            return {"error": "not_found", "vevent_id": vevent_id}
        return {**_rdv_public(v),
                "comment": v.get("comment"), "title": v.get("title"),
                "sequence": v.get("sequence"),
                "service_origin_id": v.get("service_origin_id")}

    @mcp.tool()
    async def planity_list_recurring_appointments(
        salon_id: str, employee_id: Optional[str] = None, limit: int = 50,
    ) -> list[dict]:
        """Recurring appointments (standing bookings) of the salon.

        They live in a separate node and appear in NO date-range listing: a
        calendar that only holds recurrences reads as an empty calendar. Each one
        carries an `rrule` (RFC 5545) rather than a date.

        ⚠️ Same allow-list: customer id only, no name or contact details.
        """
        c = await _client()
        out = []
        for r in await c.list_recurring_appointments(salon_id, employee_id, limit):
            out.append({
                "id": r["id"], "employee_id": r["child_id"], "rrule": r["rrule"],
                "duration_minutes": r["duration_minutes"],
                "service_id": r["service_id"], "sequence": r["sequence"],
                "price_eur": _eur_ou_rien(r["price_cents"]),
                "customer_id": r["customer_id"],
                "created_at": iso(r["created_at"]),
                "updated_at": iso(r["updated_at"]),
                "all_day": r["all_day"],
            })
        return out
