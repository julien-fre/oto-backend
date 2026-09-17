"""Nextmotion — l'organisation de l'agenda : salles, appareils, plages d'ouverture,
absences, demandes de rendez-vous en ligne, et parcours patient du jour.

Module frère de `nextmotion.py` (cf. `Connector.modules`). Les rendez-vous eux-mêmes
et les créneaux libres restent dans `nextmotion.py` (`nextmotion_appointment` porte
les deux écritures). Deux outils :

- `nextmotion_calendar` — les ressources et réglages de l'agenda, `kind` × list | get :
  tous partagent clinique + identifiant, chaque kind a au plus ses filtres (ADR 0047).
- `nextmotion_journey` — le parcours d'un patient sur un rendez-vous (étapes
  requises / faites) : une liste seule, à douze filtres qui ne recouvrent rien des
  autres, d'où un outil à part.

⚠️ Ce qui est retiré, en plus de la règle commune (`nextmotion_socle`) :
- d'une **demande en ligne** : la personne qui la fait (nom, prénom, email,
  téléphone, date de naissance, âge, sexe), son lien et son message de prépaiement ;
- d'un **évènement** (plage, absence, parcours) : titre, sous-titre, notes, statut
  de séance, textes des SMS / WhatsApp de rappel ;
- d'un **parcours** : sa consultation (un objet médical), et le filtre `search` de
  l'API, qui cherche sur le NOM du patient — comme le tri par nom.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .nextmotion_garde import Kind, _client, _need, _paging, _run, _serve_kind
from .nextmotion_socle import (_ABSENCE, _APPOINTMENT_REQUEST, _DEVICE, _JOURNEY,
                               _OPENING_HOUR, _PERSONNE, _ROOM, _TEXTES, _page, _shape)

_KINDS = {
    "room": Kind(
        "rooms", _shape(_ROOM), lambda c, cid, **p: c.list_appointment_rooms(cid, **p),
        lambda c, i: c.get_appointment_room(i)),
    "device": Kind(
        "devices", _shape(_DEVICE),
        lambda c, cid, **p: c.list_appointment_devices(cid, **p),
        lambda c, i: c.get_appointment_device(i)),
    "opening_hour": Kind(
        "opening_hours", _shape(_OPENING_HOUR),
        lambda c, cid, show_all, **p: c.list_calendar_opening_hours(
            cid, show_all=show_all, **p),
        lambda c, i: c.get_calendar_opening_hour(i), ("show_all",), _TEXTES),
    "absence": Kind(
        "absences", _shape(_ABSENCE),
        lambda c, cid, start_date, end_date, show_all, **p: c.list_calendar_absences(
            cid, start_date=start_date, end_date=end_date, show_all=show_all, **p),
        lambda c, i: c.get_calendar_absence(i), ("start_date", "end_date", "show_all"),
        _TEXTES),
    "appointment_request": Kind(
        "appointment_requests", _shape(_APPOINTMENT_REQUEST),
        lambda c, cid, request_status, **p: c.list_appointment_requests(
            cid, status=request_status, **p),
        lambda c, i: c.get_appointment_request(i), ("request_status",), _PERSONNE),
}

_journey = _shape(_JOURNEY)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_calendar(
        kind: Literal["room", "device", "opening_hour", "absence", "appointment_request"],
        op: Literal["list", "get"] = "list",
        clinic_id: Optional[str] = None,
        item_id: Optional[str] = None,
        show_all: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        request_status: Optional[Literal["new", "pending_pre_payment", "accepted",
                                         "rejected"]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """How a Nextmotion clinic's calendar is organised — rooms, devices, opening
        hours, absences — and the appointment requests made online. Appointments
        themselves: `nextmotion_appointment`; free slots: `nextmotion_availability`.

        `kind`:
        - "room" — appointment rooms (online booking, visit types served).
        - "device" — appointment devices (availability, visit types served).
        - "opening_hour" — opening-hour events (slots, visit types, practitioners,
          recurrence); filter `show_all`.
        - "absence" — practitioners' absences (dates, recurrence); filters
          `start_date`, `end_date`, `show_all`.
        - "appointment_request" — online booking requests (requested slot, visit
          type, practitioner); filter `request_status`. The requesting person is
          NOT served: no name, email, phone or birth date, and nothing identifies them.
        Event titles, notes and reminder texts are withheld.

        `op`: **"list"** (default, needs `clinic_id`) | **"get"** (`item_id`).
        Each filter belongs to its kinds only; the others refuse it.

        Args:
            kind: which calendar object.
            op: list (default) | get.
            clinic_id: op="list" — the clinic.
            item_id: op="get" — the object of that kind.
            show_all: op="list", opening_hour/absence — every one of the clinic, not
                only the key user's (Nextmotion default: false).
            start_date / end_date: op="list", absence — YYYY-MM-DD.
            request_status: op="list", appointment_request — new |
                pending_pre_payment | accepted | rejected.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the default view.
        """
        filters = {"show_all": show_all, "start_date": start_date, "end_date": end_date,
                   "request_status": request_status}
        return _serve_kind(_KINDS, kind, op, client=_client, clinic_id=clinic_id,
                           item_id=item_id, filters=filters, limit=limit, offset=offset,
                           fields=fields)

    @mcp.tool()
    def nextmotion_journey(
        clinic_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        include_ongoing: Optional[bool] = None,
        status: Optional[Literal["not_started", "in_progress", "finished"]] = None,
        doctor_ids: Optional[list[str]] = None,
        visit_type_ids: Optional[list[str]] = None,
        sub_visit_type_ids: Optional[list[str]] = None,
        patient_id: Optional[str] = None,
        order: Optional[Literal["start_time", "-start_time"]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Patient journeys of a Nextmotion clinic — for each appointment, the steps
        required and completed, visit type, room and schedule. Read only.

        The patient is served by their ID ONLY — no name, email or phone, and no tool
        resolves that id to a person. The consultation, event titles and notes are
        withheld; searching or sorting by patient name is not offered.

        Args:
            clinic_id: the clinic.
            start_date / end_date: ISO 8601 date (YYYY-MM-DD) or date-time.
            include_ongoing: include journeys overlapping a bound (Nextmotion
                default: true).
            status: not_started | in_progress | finished.
            doctor_ids / visit_type_ids / sub_visit_type_ids: only these (UUID lists).
            patient_id: only this patient's journeys.
            order: start_time | -start_time.
            limit / offset: pagination (limit 1..100, default 50).
            fields: keep only these keys per row (`id` always kept); omitted or
                `["*"]` = the default view.
        """
        _need("list", clinic_id=clinic_id)
        c = _client()
        return _page(_run(lambda: c.list_calendar_journeys(
            clinic_id, start_date=start_date, end_date=end_date,
            include_ongoing=include_ongoing, status=status, doctor_ids=doctor_ids,
            visit_type_ids=visit_type_ids, sub_visit_type_ids=sub_visit_type_ids,
            patient_id=patient_id, order=order, **_paging(limit, offset))),
            "journeys", _journey, fields=fields)
