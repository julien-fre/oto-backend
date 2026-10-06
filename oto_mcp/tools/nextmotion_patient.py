"""Nextmotion — the patient's IDENTITY: list (with search), record, creation,
modification.

Sibling module of `nextmotion.py` (see `Connector.modules`). One tool,
`nextmotion_patient`, and it is the ONLY one of the connector that serves a person:
everywhere else, an appointment, a quote, an invoice, a payment or a journey carries the
patient only by id — this is where that id is resolved.

What comes out (`_PATIENT_IDENTITY`): last name, first name, email, phone, date of birth,
age, gender, address, postal code, city, country, contact consents, patient
number, archived. What does not come out, even here: the practitioner's comments
(`doctor_comments`, clinical text), the photograph, the GPS coordinates. What does not
go in: `doctor_comments` (refused by name, `nextmotion_entrees._IN_PATIENT`).

The medical record around the patient (history, media, prescriptions, treatments,
consultations, visits) stays outside the connector, as does deleting a patient.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .nextmotion_entrees import _IN_PATIENT, _REQUIRED_PATIENT
from .nextmotion_garde import Kind, _client, _crud, _serve
from .nextmotion_socle import _IDENTITE, _PATIENT_IDENTITY, _shape

_PATIENTS = {"patient": Kind(
    "patients", _shape(_PATIENT_IDENTITY),
    lambda c, cid, search, birth_date, phone_number, invoice_total_gt, is_archived, **p:
        c.list_patients(cid, search=search, birth_date=birth_date,
                        phone_number=phone_number, invoice_total_gt=invoice_total_gt,
                        is_archived=is_archived, **p),
    lambda c, i: c.get_patient(i),
    ("search", "birth_date", "phone_number", "invoice_total_gt", "is_archived"),
    _IDENTITE,
    _crud(lambda c, cid, b: c.create_patient(cid, body=b),
          lambda c, i, b: c.update_patient(i, body=b), None, _IN_PATIENT, _REQUIRED_PATIENT),
)}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_patient(
        op: Literal["list", "get", "create", "update"] = "list",
        clinic_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        search: Optional[str] = None,
        birth_date: Optional[str] = None,
        phone_number: Optional[str] = None,
        invoice_total_gt: Optional[str] = None,
        is_archived: Optional[bool] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Patients of a Nextmotion clinic — their IDENTITY only: name, email, phone,
        birth date, age, gender, postal address, zip code, city, country, contact
        consents, patient number, archived flag. This tool resolves the patient id
        served by appointments, quotes, invoices, payments and journeys.
        Never served, even here: the practitioner's comments, the photograph, GPS
        coordinates, the medical file. A patient cannot be deleted.

        `op`: "list" (default, `clinic_id`; filters `search` — part of the name, birth
        date, phone or id —, `birth_date` YYYY-MM-DD or MM-DD, `phone_number`,
        `invoice_total_gt` e.g. "100.00", `is_archived`) | "get" (`patient_id`) |
        "create" (`clinic_id` + `data`) | "update" (`patient_id` + `data`); `email`,
        `first_name`, `last_name`, `gender` required on both. `doctor_comments` and any
        other unlisted field are refused, with the accepted list.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act.

        Args:
            op: list (default) | get | create | update.
            clinic_id: op="list"/"create".
            patient_id: op="get"/"update".
            search / birth_date / phone_number / invoice_total_gt / is_archived: list filters.
            data: op="create"/"update" — the patient's fields.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        filters = {"search": search, "birth_date": birth_date,
                   "phone_number": phone_number, "invoice_total_gt": invoice_total_gt,
                   "is_archived": is_archived}
        return _serve(_PATIENTS, "patient", op, client=_client, clinic_id=clinic_id,
                      item_id=patient_id, filters=filters, limit=limit, offset=offset,
                      fields=fields, data=data, dry_run=dry_run, item_name="patient_id")
