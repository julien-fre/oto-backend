"""Nextmotion — logiciel de gestion de cliniques de médecine esthétique, côté
ADMINISTRATIF, en lecture (plus deux gestes d'agenda) : cliniques, praticiens, agenda,
catalogue, ventes, leads, statistiques, stock, réglages.

Wrappe `oto.tools.nextmotion.NextmotionClient` (Bearer, API « External » v4). keyed
`api_key`, BYO (membre ou org) : une clé agit au nom de l'utilisateur qui l'a générée,
sur les cliniques dont il est employé — il n'y a pas de clé plateforme.

## Données de santé : ce que ce connecteur ne sert PAS

Nextmotion porte des dossiers patients. Tout ce qui est contenu médical — dossier et
antécédents, photos et médias, ordonnances, consentements, soins réalisés,
consultations, visites (notes cliniques), questionnaires de santé, suivi post-soin —
est **hors périmètre**, comme la liste et la fiche des patients et le chat : le client
oto-core n'a aucune méthode vers ces endpoints, et ces modules n'en ajoutent pas. Les
ouvrir est une décision de gouvernance (RGPD art. 9, hébergement HDS), pas une
extension de surface.

⚠️ **Des ressources du périmètre EMBARQUENT quand même de la donnée personnelle** :
rendez-vous, parcours, devis, factures et paiements portent un objet `patient` complet ;
une demande de rendez-vous en ligne et un lead portent le nom, l'email et le téléphone
de la personne ; beaucoup portent du texte libre. **Tout ce qui sort passe donc par une
LISTE BLANCHE** (`nextmotion_socle`) : seuls les champs nommés passent, un champ que
l'API ajouterait demain reste dehors. **Le patient n'est servi que par son `id`**, la
personne d'une demande ou d'un lead pas du tout. **Il n'existe aucune échappatoire vers
le brut** (`fields=["*"]` rend la vue par défaut), aucun outil ne résout un id patient
en identité, et aucun filtre qui cherche sur le nom d'une personne n'est exposé.

## Surface (ADR 0047), verbe en `op`, défaut toujours en lecture

Ce module :
- `nextmotion_clinic` — découverte : les cliniques de la clé (seul, sans op).
- `nextmotion_practitioner` — list | get.
- `nextmotion_appointment` — list | get | reschedule | delete. Les deux écritures
  touchent un vrai patient : `dry_run` vaut **True par défaut** sur elles et rend
  l'état actuel du rendez-vous sans rien écrire.
- `nextmotion_availability` — créneaux libres (params disjoints de l'agenda, d'où
  un tool à part) ; fournit l'`id` et le `time_slot` qu'exige `reschedule`.
- `nextmotion_quote`, `nextmotion_invoice` — list | get ; filtre de période des
  factures appliqué CÔTÉ OUTIL → `nextmotion_periode` (pas sur les devis : `OApiQuote`
  n'a pas d'`invoiced_time`, son `issued_time` est nullable).
- `nextmotion_product` — stock (lots), list | get, NON rattaché aux factures.

Modules frères (même clé, même client, montés par `Connector.modules`) :
`nextmotion_catalogue` (catalogue, forfaits, répartitions comptables, produits
globaux), `nextmotion_agenda` (salles, appareils, plages, absences, demandes en ligne,
parcours), `nextmotion_ventes` (paiements, statistiques, totaux d'un patient),
`nextmotion_crm` (leads, réglages de la clinique).

**Aucun argument n'est retenu au silence** (`is not None`) → `nextmotion_garde`.

Dérivé de la spec OpenAPI publique (lue le 2026-09-17). **Aucun appel réel** : pas
de clé disponible — la forme exacte des réponses, les effets de bord d'une
suppression ou d'un report (notification au patient ?) ne sont pas vérifiés.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from ..connectors import verify as connector_verify
from . import nextmotion_periode as periode
from .nextmotion_garde import (_NAME, _bad, _client, _need, _paging, _refuse_ignored, _run,
                               _verify)
from .nextmotion_socle import (_CLINIC, _COLLAB, _appointment, _invoice, _one, _page,
                               _product, _quote, _shape)

_clinic = _shape(_CLINIC)
_collab = _shape(_COLLAB)
_slot = _shape(("id", "type", "time_slot", "utc_offset"))


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
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Practitioners (doctors, staff) of a Nextmotion clinic.

        `op`:
        - **"list"** (default): the clinic's practitioners (`clinic_id`).
        - **"get"**: one practitioner (`doctor_id`).

        Args:
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            doctor_id: op="get" — the practitioner.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
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
        raise _bad("op doit être 'list' ou 'get'.")

    @mcp.tool()
    def nextmotion_appointment(
        op: Literal["list", "get", "reschedule", "delete"] = "list",
        clinic_id: Optional[str] = None,
        appointment_id: Optional[str] = None,
        date: Optional[str] = None,
        patient_id: Optional[str] = None,
        visit_type_opening_hour_id: Optional[str] = None,
        time_slot: Optional[str] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Calendar appointments of a Nextmotion clinic — read the agenda, move or
        delete an appointment.

        Health data is withheld: each appointment keeps its schedule, status, visit
        type, room and practitioners. The patient is served by their ID ONLY — no
        name, email or phone, and no tool resolves that id to a person: do not try
        to infer who the patient is. No title, notes or anything clinical.

        `op`:
        - **"list"** (default): the clinic's appointments (`clinic_id`), optionally
          on one `date` (YYYY-MM-DD) or for one `patient_id`.
        - **"get"**: one appointment (`appointment_id`).
        - **"reschedule"**: moves `appointment_id` to a free slot —
          `visit_type_opening_hour_id` and `time_slot` both come from ONE entry of
          `nextmotion_availability`. ⚠️ Touches a real patient.
        - **"delete"**: deletes `appointment_id`. ⚠️ Touches a real patient; whether
          Nextmotion notifies them is not documented.

        ⚠️ `dry_run` DEFAULTS TO TRUE on reschedule/delete: the call returns the
        appointment as it stands and what would change, and writes nothing. Pass
        `dry_run=False` deliberately to act.

        Args:
            op: list (default) | get | reschedule | delete.
            clinic_id: op="list" — the clinic.
            appointment_id: op="get"/"reschedule"/"delete".
            date: op="list" — YYYY-MM-DD.
            patient_id: op="list" — only this patient's appointments.
            visit_type_opening_hour_id: op="reschedule" — slot `id`.
            time_slot: op="reschedule" — slot `time_slot` (date-time).
            dry_run: op="reschedule"/"delete" — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
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
            raise _bad("op doit être 'list', 'get', 'reschedule' ou 'delete'.")
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
                       "note": f"Rien n'est écrit. Repasse avec dry_run=False pour {op}."}
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
    def nextmotion_quote(
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        quote_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Quotes (devis) of a Nextmotion clinic — number, status, lines (with
        sub-pricings, markup, accounting codes), totals, follow-up and channel.
        Health data, titles and free text are withheld; the patient is served by
        their ID ONLY (no name, email or phone; no tool resolves it to a person).

        Status codes: 1 NEW, 2 QUOTED, 3 ACCEPTED, 4 REJECTED, 5 INVOICED,
        6 ACQUAINTED.

        `op`: **"list"** (default, `clinic_id`, optional `patient_id`) |
        **"get"** (`quote_id`).

        Args:
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            quote_id: op="get" — the quote.
            patient_id: op="list" — only this patient's quotes.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, quote_id=quote_id)
            return _page(_run(lambda: c.list_quotes(
                clinic_id, patient_id=patient_id, **_paging(limit, offset))),
                "quotes", _quote, fields=fields)
        if op == "get":
            _need(op, quote_id=quote_id)
            _refuse_ignored(op, clinic_id=clinic_id, patient_id=patient_id,
                            limit=limit, offset=offset, fields=fields)
            return _one(_run(lambda: c.get_quote(quote_id)), "quote", _quote)
        raise _bad("op doit être 'list' ou 'get'.")

    @mcp.tool()
    def nextmotion_invoice(
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        invoice_id: Optional[str] = None,
        invoiced_from: Optional[str] = None,
        invoiced_to: Optional[str] = None,
        max_pages: Optional[int] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Invoices of a Nextmotion clinic — number, status, lines, totals, payment
        methods. Health data, titles and free text are withheld; the patient
        is served by their ID ONLY (no name, email or phone; no tool resolves it to a person).

        Status codes: 2 NEW, 3 VALIDATED, 4 NEW_ONLY_DEPOSITS, 5 NEW_ISSUED,
        6 NEW_DEPOSITS_PAID, 9 ONLY_DEPOSITS, 10 ISSUED, 11 DEPOSITS_PAID,
        12 NEUTRALIZED.

        `op`: **"list"** (default, `clinic_id`) | **"get"** (`invoice_id`).

        Period filter (op="list"): `invoiced_from` / `invoiced_to` (YYYY-MM-DD, both
        inclusive, on `invoiced_time`). Nextmotion neither filters nor promises an
        order, so the tool reads EVERY page (100 invoices per call, up to `max_pages`)
        and says what it did: `pages_lues`, `factures_parcourues`, `complet`.
        `complet: false` = the page cap cut the scan, the result is PARTIAL: call again
        with `offset=offset_suivant` and the same period. With a period, `limit` is
        refused and `offset` is where the scan starts in the upstream list.
        Lines carry the act name, price, quantity, rebate, markup, VAT and their
        sub-pricings (accounting code, clinic/provider split); no consumables nor
        lots per invoice in the API (`nextmotion_product` is not linked to invoices).
        Payments of an invoice: `nextmotion_payment(invoice_id=…)`.

        Args:
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            invoice_id: op="get" — the invoice.
            invoiced_from / invoiced_to: op="list" — period bounds, YYYY-MM-DD.
            max_pages: op="list" with a period — page cap, 1..100 (default 20).
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, invoice_id=invoice_id)
            if invoiced_from is None and invoiced_to is None:
                if max_pages is not None:
                    raise _bad(f"op={op!r} n'utilise pas `max_pages` sans "
                               "`invoiced_from`/`invoiced_to`.")
                return _page(_run(lambda: c.list_invoices(
                    clinic_id, **_paging(limit, offset))), "invoices", _invoice,
                    fields=fields)
            if limit is not None:
                raise _bad(f"op={op!r} n'utilise pas `limit` avec une période : toutes les "
                           "factures de la période lues sont rendues ; `offset` y est le "
                           "point de départ du parcours.")
            return _run(lambda: periode.lister(
                lambda o: c.list_invoices(clinic_id, limit=periode.PAGE, offset=o),
                invoiced_from, invoiced_to, offset=0 if offset is None else offset,
                max_pages=max_pages, fields=fields))
        if op == "get":
            _need(op, invoice_id=invoice_id)
            _refuse_ignored(op, clinic_id=clinic_id, limit=limit, offset=offset,
                            fields=fields, invoiced_from=invoiced_from,
                            invoiced_to=invoiced_to, max_pages=max_pages)
            return _one(_run(lambda: c.get_invoice(invoice_id)), "invoice", _invoice)
        raise _bad("op doit être 'list' ou 'get'.")

    @mcp.tool()
    def nextmotion_product(
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        product_id: Optional[str] = None,
        search: Optional[str] = None,
        stock_state: Optional[Literal["low", "out", "ok"]] = None,
        expiring_within_days: Optional[int] = None,
        order: Optional[Literal["name", "-name", "brand_name", "-brand_name", "stock_level",
                                "-stock_level", "warning_level", "-warning_level"]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Product stock of a Nextmotion clinic, read only — one row per lot: lot
        number, expiration date, digital and physical stock levels, warning level,
        unit price, catalogue product (`global_product`: name, brand).
        ⚠️ The stock is not linked to invoices, quotes or treatments: the Nextmotion
        API does not expose which consumables or lots an invoice used. Never pair a
        lot with an invoice or a patient.
        `op`: **"list"** (default, `clinic_id`, filters) | **"get"** (`product_id`).

        Args:
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            product_id: op="get" — the product (lot).
            search: op="list" — free-text search.
            stock_state: op="list" — low | out | ok.
            expiring_within_days: op="list" — lots expiring within N days (>= 1).
            order: op="list" — sort (default brand_name; `-` = descending).
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
        c = _client()
        if op == "list":
            _need(op, clinic_id=clinic_id)
            _refuse_ignored(op, product_id=product_id)
            if expiring_within_days is not None and expiring_within_days < 1:
                raise _bad(f"expiring_within_days doit être >= 1 — reçu {expiring_within_days}.")
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
        raise _bad("op doit être 'list' ou 'get'.")
