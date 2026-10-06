"""Nextmotion — management software for aesthetic-medicine clinics: the whole
ADMINISTRATIVE side, read AND write (clinics, practitioners, calendar, catalogue,
sales, leads, calls and messages, statistics, stock, settings), plus the patient's
IDENTITY (`nextmotion_patient`).

Wraps `oto.tools.nextmotion.NextmotionClient` (Bearer, "External" API v4). keyed
`api_key`, BYO (member or org): a key acts on behalf of the user who generated it,
on the clinics where they are employed — there is no platform key.

## Health data: what this connector does NOT serve

Nextmotion holds patient files. Everything that is medical content — history,
photos and media, prescriptions and their signature, signed consents, treatments performed,
consultations, visits (clinical notes), quotes and invoices created under a
consultation — is **out of scope**, like the chat: the oto-core client has no
method toward those endpoints, and these modules add none. Opening them is a
governance decision (GDPR art. 9, HDS hosting), not an extension of the surface.
Also not deletable: a patient, an invoice, a payment.

**The patient's identity is served, by a single tool** (owner's decision,
2026-10-01): `nextmotion_patient` reads (list with search, record), creates and edits
last name, first name, email, phone, date of birth, age, gender, address, contact
consents, patient number, archived — never the practitioner's comments, the photo or
the GPS coordinates. `nextmotion_analyse` reads the same list for threshold aggregates.

⚠️ **Some in-scope resources EMBED personal data**: appointments,
journeys, quotes, invoices and payments carry a full `patient` object; an online
appointment request and a lead carry the person's name, email and phone;
many carry free text. **Everything that comes out therefore goes through an ALLOWLIST**
(`nextmotion_socle`): only the named fields pass, a field the API
added tomorrow stays out. **Outside `nextmotion_patient`, the patient is served
only by their `id`** (which resolves there); a lead serves its contact identity (name, email,
phone), never its notes; the person of an online request not at all. **There
is no escape hatch to the raw data** (`fields=["*"]` returns the default view), and
no filter that searches on a person's name is exposed outside the patient
list.

## Writes: a preview unless told otherwise

Every write (create, update, delete, and the specific verbs: reschedule, validate,
pay, convert…) has **`dry_run=True` by default**: the tool validates the arguments, re-reads
the targeted object (projected) and returns what would go out, without calling any write method.
**Never an implicit notification**: the send flags the spec sets to `true` by
default (editing an appointment) go out as `false` unless explicitly requested, and
the preview says who would be notified. The body goes in `data`, validated against the op's
INPUT allowlist (`nextmotion_entrees`, drawn from the spec's `requestBody`):
**an unknown field is rejected by name**, never ignored. A write's response
goes back through the resource's allowlist. Shared mechanics → `nextmotion_garde`
(`Write`, `_serve_write`).

## Surface (ADR 0047), verb in `op`, default always read

This module:
- `nextmotion_clinic` — discovery: the key's clinics (alone, no op).
- `nextmotion_practitioner` — list | get | create | update | delete.
- `nextmotion_appointment` — list | get | update | reschedule | delete.
- `nextmotion_availability` — free slots (params disjoint from the calendar, hence
  a separate tool); provides the `id` and `time_slot` that `reschedule` and an
  online appointment request require.
- `nextmotion_product` — stock (batches), list | get | create | update | delete, NOT
  attached to invoices.

Sibling modules (same key, same client, mounted by `Connector.modules`):
`nextmotion_catalogue` (catalogue, packages, accounting distributions, post-treatment
configuration), `nextmotion_agenda` (rooms, devices, slots, absences, online
requests, journeys), `nextmotion_ventes` (quotes, invoices and credit notes, payments,
statistics, a patient's totals), `nextmotion_crm` (leads, calls and messages,
settings, questionnaire models, webhooks), `nextmotion_patient` (the identity),
`nextmotion_analyse` (clientele and device occupancy, as aggregates).

**No argument is silently dropped** (`is not None`) → `nextmotion_garde`.

Derived from the public OpenAPI spec (read on 2026-09-17, writes on 2026-10-01).
**No real call**: no key available — the exact shape of responses and the side
effects of a write (notification to the patient?) are not verified.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from ..connectors import verify as connector_verify
from .nextmotion_entrees import (_IN_APPOINTMENT, _IN_DOCTOR_CREATE, _IN_DOCTOR_UPDATE,
                                 _IN_PRODUCT_CREATE, _IN_PRODUCT_UPDATE,
                                 _NO_NOTIFY_APPOINTMENT)
from .nextmotion_garde import (_NAME, Kind, Write, _bad, _client, _crud, _need, _paging,
                               _refuse_ignored, _run, _serve_write, _verify)
from .nextmotion_socle import (_CLINIC, _COLLAB, _WITHHELD, _appointment, _one, _page,
                               _product, _shape)

_clinic = _shape(_CLINIC)
_collab = _shape(_COLLAB)
_slot = _shape(("id", "type", "time_slot", "utc_offset"))

_PRACTITIONERS = {"practitioner": Kind(
    "practitioners", _collab, lire=lambda c, i: c.get_doctor(i),
    writes=_crud(lambda c, cid, b: c.create_doctor(cid, body=b),
                 lambda c, i, b: c.update_doctor(i, body=b),
                 lambda c, i, b: c.delete_doctor(i), _IN_DOCTOR_CREATE, ("email", "kind"),
                 accepted_update=_IN_DOCTOR_UPDATE, required_update=("speciality",)))}
_APPOINTMENTS = {"appointment": Kind(
    "appointments", _appointment, lire=lambda c, i: c.get_appointment(i),
    withheld=_WITHHELD,
    writes={"update": Write(lambda c, i, b: c.update_appointment(i, body=b), "item",
                            _IN_APPOINTMENT, ("calendar_event",),
                            defaults=_NO_NOTIFY_APPOINTMENT,
                            notify=tuple(_NO_NOTIFY_APPOINTMENT))})}
_PRODUCTS = {"product": Kind(
    "products", _product, lire=lambda c, i: c.get_product(i),
    writes=_crud(lambda c, cid, b: c.create_product(cid, body=b),
                 lambda c, i, b: c.update_product(i, body=b),
                 lambda c, i, b: c.delete_product(i), _IN_PRODUCT_CREATE,
                 ("global_product",), accepted_update=_IN_PRODUCT_UPDATE,
                 required_update=()))}


def register(mcp: FastMCP) -> None:
    connector_verify.register(_NAME, _verify)

    @mcp.tool()
    def nextmotion_clinic(limit: Optional[int] = None, offset: Optional[int] = None,
                          fields: Optional[list] = None) -> dict:
        """The Nextmotion clinics the API key's user belongs to — start here: every
        other nextmotion tool needs a `clinic_id` from this list.

        Args:
            limit: 1..100 (default 50).
            offset: pagination start (default 0).
            fields: keep only these keys per clinic (`id` always kept).
        """
        c = _client()
        return _page(_run(lambda: c.list_clinics(**_paging(limit, offset))), "clinics",
                     _clinic, fields=fields, withheld=None)

    @mcp.tool()
    def nextmotion_practitioner(
        op: Literal["list", "get", "create", "update", "delete"] = "list",
        clinic_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Practitioners (doctors, staff) of a Nextmotion clinic — read and write.

        `op`: "list" (default, `clinic_id`) | "get" (`doctor_id`) | "create" (`clinic_id`
        + `data`: `email`, `kind` required; names when the email is unknown) | "update"
        (`doctor_id` + `data`, `speciality` required) | "delete" (`doctor_id`). `data` takes
        the fields the Nextmotion spec accepts for the op; any other field is refused.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act.

        Args:
            op: list (default) | get | create | update | delete.
            clinic_id: op="list"/"create".
            doctor_id: op="get"/"update"/"delete".
            data: op="create"/"update" — the fields to send.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        if op in ("create", "update", "delete"):
            return _serve_write(_PRACTITIONERS, "practitioner", op, client=_client,
                                clinic_id=clinic_id, item_id=doctor_id, data=data,
                                dry_run=dry_run, item_name="doctor_id",
                                unused={"limit": limit, "offset": offset, "fields": fields})
        _refuse_ignored(op, data=data, dry_run=dry_run)
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, doctor_id=doctor_id)
            return _page(_run(lambda: c.list_doctors(clinic_id, **_paging(limit, offset))),
                         "practitioners", _collab, fields=fields, withheld=None)
        if op == "get":
            _need(op, doctor_id=doctor_id)
            _refuse_ignored(op, clinic_id=clinic_id, limit=limit, offset=offset,
                            fields=fields)
            return _one(_run(lambda: c.get_doctor(doctor_id)), "practitioner", _collab,
                        withheld=None)
        raise _bad("op must be 'list', 'get', 'create', 'update' or 'delete'.")

    @mcp.tool()
    def nextmotion_appointment(
        op: Literal["list", "get", "update", "reschedule", "delete"] = "list",
        clinic_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        date: Optional[str] = None,
        patient_id: Optional[str] = None,
        visit_type_opening_hour_id: Optional[str] = None,
        time_slot: Optional[str] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Calendar appointments of a Nextmotion clinic — read the agenda, update, move
        or delete an appointment.

        Health data is withheld: schedule, status, visit type, room and practitioners
        stay. The patient is served by ID ONLY — resolve it with
        `nextmotion_patient(op="get")` when needed. No title, notes or anything clinical.

        `op`:
        - "list" (default, `clinic_id`, optional `date` YYYY-MM-DD or `patient_id`) |
          "get" (`appointment_id`).
        - "update" (`appointment_id` + `data`, `calendar_event` with `start_time` /
          `end_time` required; the link to a clinical visit is refused). ⚠️ Nextmotion
          would email/SMS the patient by default: this tool sends
          `send_appointment_modified_email` / `_sms` = false unless you pass true; the
          preview lists who is notified (`notifie_le_patient`). `data` takes the fields the
          Nextmotion spec accepts for the op; any other field is refused.
        - "reschedule" (`appointment_id`, `visit_type_opening_hour_id` + `time_slot` from
          ONE `nextmotion_availability` entry). ⚠️ Touches a real patient.
        - "delete" (`appointment_id`). ⚠️ Touches a real patient; whether Nextmotion
          notifies them is not documented.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act.

        Args:
            op: list (default) | get | update | reschedule | delete.
            clinic_id: op="list".
            appointment_id: every op but list.
            date: op="list" — YYYY-MM-DD.
            patient_id: op="list" — only this patient's appointments.
            visit_type_opening_hour_id: op="reschedule" — slot `id`.
            time_slot: op="reschedule" — slot `time_slot` (date-time).
            data: op="update" — the fields to send.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        if op == "update":
            return _serve_write(
                _APPOINTMENTS, "appointment", op, client=_client, clinic_id=clinic_id,
                item_id=appointment_id, data=data, dry_run=dry_run,
                item_name="appointment_id",
                unused={"date": date, "patient_id": patient_id, "limit": limit,
                        "offset": offset, "fields": fields,
                        "visit_type_opening_hour_id": visit_type_opening_hour_id,
                        "time_slot": time_slot})
        _refuse_ignored(op, data=data)
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, appointment_id=appointment_id,
                            visit_type_opening_hour_id=visit_type_opening_hour_id,
                            time_slot=time_slot, dry_run=dry_run)
            return _page(_run(lambda: c.list_appointments(
                clinic_id, date=date, patient_id=patient_id, **_paging(limit, offset))),
                "appointments", _appointment, fields=fields)
        if op not in ("get", "reschedule", "delete"):
            raise _bad("op must be 'list', 'get', 'update', 'reschedule' or 'delete'.")
        _refuse_ignored(op, clinic_id=clinic_id, date=date, patient_id=patient_id,
                        limit=limit, offset=offset, fields=fields)
        _need(op, appointment_id=appointment_id)
        if op == "get":
            _refuse_ignored(op, visit_type_opening_hour_id=visit_type_opening_hour_id,
                            time_slot=time_slot, dry_run=dry_run)
            return _one(_run(lambda: c.get_appointment(appointment_id)),
                        "appointment", _appointment)
        if op == "reschedule":
            _need(op, visit_type_opening_hour_id=visit_type_opening_hour_id,
                  time_slot=time_slot)
        else:
            _refuse_ignored(op, visit_type_opening_hour_id=visit_type_opening_hour_id,
                            time_slot=time_slot)
        if dry_run is None or dry_run:
            current = _one(_run(lambda: c.get_appointment(appointment_id)),
                           "appointment", _appointment)
            preview = {"dry_run": True, "would": op, **current,
                       "note": f"Nothing is written. Call again with dry_run=False to {op}."}
            if op == "reschedule":
                preview["to"] = {"visit_type_opening_hour_id": visit_type_opening_hour_id,
                                 "time_slot": time_slot}
            return preview
        if op == "reschedule":
            return _one(_run(lambda: c.reschedule_appointment(
                appointment_id, visit_type_opening_hour_id=visit_type_opening_hour_id,
                time_slot=time_slot)), "appointment", _appointment)
        _run(lambda: c.delete_appointment(appointment_id))
        return {"deleted": True, "appointment_id": appointment_id}

    @mcp.tool()
    def nextmotion_availability(
        clinic_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sub_visit_type_id: Optional[str] = None,
        sub_visit_type_name: Optional[str] = None,
        doctor_id: Optional[str] = None,
        doctor_name: Optional[str] = None,
    ) -> dict:
        """Free appointment slots of a Nextmotion clinic. Each slot's `id` and
        `time_slot` are what `nextmotion_appointment(op="reschedule")` needs.

        Args:
            clinic_id: the clinic.
            start_date / end_date: YYYY-MM-DD; omitted, Nextmotion searches the
                current month.
            sub_visit_type_id / sub_visit_type_name: only slots for this sub visit
                type (name = exact match).
            doctor_id / doctor_name: only slots of this practitioner (name = exact).
        """
        c = _client()
        env = _run(lambda: c.search_time_slots(
            clinic_id, start_date=start_date, end_date=end_date,
            sub_visit_type_id=sub_visit_type_id, sub_visit_type_name=sub_visit_type_name,
            doctor_id=doctor_id, doctor_name=doctor_name))
        return {"slots": [_slot(r) for r in (env or {}).get("data") or []]}

    @mcp.tool()
    def nextmotion_product(
        op: Literal["list", "get", "create", "update", "delete"] = "list",
        clinic_id: Optional[str] = None,
        product_id: Optional[str] = None,
        search: Optional[str] = None,
        stock_state: Optional[Literal["low", "out", "ok"]] = None,
        expiring_within_days: Optional[int] = None,
        order: Optional[Literal["name", "-name", "brand_name", "-brand_name", "stock_level",
                                "-stock_level", "warning_level", "-warning_level"]] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Product stock of a Nextmotion clinic — one row per lot: lot number,
        expiration date, digital and physical stock levels, warning level, unit price,
        catalogue product (`global_product`: name, brand).
        ⚠️ The stock is not linked to invoices, quotes or treatments: the Nextmotion API
        does not expose which consumables or lots an invoice used. Never pair a lot with
        an invoice or a patient.

        `op`: "list" (default, `clinic_id`, filters) | "get" (`product_id`) | "create"
        (`clinic_id` + `data`, `global_product` required) | "update" (`product_id` +
        `data`) | "delete" (`product_id`; answers the deleted lot). `data` takes the fields
        the Nextmotion spec accepts for the op; any other field is refused.
        ⚠️ `dry_run` DEFAULTS TO TRUE on writes.

        Args:
            op: list (default) | get | create | update | delete.
            clinic_id: op="list"/"create".
            product_id: op="get"/"update"/"delete" — the lot.
            search: op="list" — free-text search.
            stock_state: op="list" — low | out | ok.
            expiring_within_days: op="list" — lots expiring within N days (>= 1).
            order: op="list" — sort (default brand_name; `-` = descending).
            data: op="create"/"update" — the fields to send.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        if op in ("create", "update", "delete"):
            return _serve_write(
                _PRODUCTS, "product", op, client=_client, clinic_id=clinic_id,
                item_id=product_id, data=data, dry_run=dry_run, item_name="product_id",
                unused={"search": search, "stock_state": stock_state,
                        "expiring_within_days": expiring_within_days, "order": order,
                        "limit": limit, "offset": offset, "fields": fields})
        _refuse_ignored(op, data=data, dry_run=dry_run)
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, product_id=product_id)
            if expiring_within_days is not None and expiring_within_days < 1:
                raise _bad(f"expiring_within_days must be >= 1 — got {expiring_within_days}.")
            return _page(_run(lambda: c.list_products(
                clinic_id, search=search, stock_state=stock_state,
                expiring_within_days=expiring_within_days, order=order,
                **_paging(limit, offset))), "products", _product, fields=fields,
                withheld=None)
        if op == "get":
            _need(op, product_id=product_id)
            _refuse_ignored(op, clinic_id=clinic_id, search=search, stock_state=stock_state,
                            expiring_within_days=expiring_within_days, order=order,
                            limit=limit, offset=offset, fields=fields)
            return _one(_run(lambda: c.get_product(product_id)), "product", _product,
                        withheld=None)
        raise _bad("op must be 'list', 'get', 'create', 'update' or 'delete'.")
