"""Nextmotion — leads (prospects), the call and message log, and a clinic's
settings.

Sibling module of `nextmotion.py` (see `Connector.modules`). Three tools:

- `nextmotion_lead` — list | get | create | update | delete | convert. A lead IS a
  person: their contact identity (last name, first name, email, phone) is served
  (decision of 2026-10-01), their notes and external reference (an identifier at a
  third party) can be written but are never read back. The pipeline: source, status, channel,
  desired treatment and zone (clinic labels), follow-ups, scheduled appointment,
  assigned practitioner. The API's `search` filter (name, email, phone) is not
  exposed. `convert` returns the created patient by id alone.
- `nextmotion_setting` — settings, `kind` × list | get | create | update | delete
  (+ `duplicate` of a document template, `placeholders` of merge fields):
  Nextmotion subscription (`feature`), custom payment means, labels,
  communication and document templates, questionnaire models (`survey_form`:
  BoltNote notes and treatment consents — the MODEL, never a patient's answer),
  webhooks. Templates and models come out as METADATA (their body is a free
  object not described by the spec) but are written whole; a webhook's `headers`
  can be written and never come out (recipient's secret).
- `nextmotion_communication` — create only: log a call, SEND a message
  (email, SMS, WhatsApp) drawn from a template, on a quote, an invoice or an
  administrative document. Neither the number, nor the notes, nor the transcript of a call, nor the
  recipient of a message come back out.

Every write has `dry_run=True` by default and its `data` passes the input allowlist
(`nextmotion_entrees`).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .nextmotion_entrees import (_COMMUNICATION_TYPES, _IN_CALL, _IN_COMMUNICATION_RECORD,
                                 _IN_COMMUNICATION_TEMPLATE, _IN_DOCUMENT_TEMPLATE_CREATE,
                                 _IN_DOCUMENT_TEMPLATE_UPDATE, _IN_LEAD,
                                 _IN_PAYMENT_MEDIUM, _IN_SURVEY_FORM_CREATE,
                                 _IN_SURVEY_FORM_UPDATE, _IN_WEBHOOK_CREATE,
                                 _IN_WEBHOOK_UPDATE)
from .nextmotion_garde import (Kind, Write, _bad, _client, _crud, _need, _refuse_ignored,
                               _run, _serve, _serve_write)
from .nextmotion_socle import (_CALL, _COMMUNICATION_RECORD, _COMMUNICATION_TEMPLATE,
                               _DOCUMENT_TEMPLATE, _FEATURE, _LABEL, _LEAD, _LEAD_RETIRE,
                               _NAMED, _PATIENT, _PLACEHOLDERS, _SURVEY_FORM, _WEBHOOK,
                               _WITHHELD, _shape)

_LEADS = {"lead": Kind(
    "leads", _shape(_LEAD), lambda c, cid, **p: c.list_leads(cid, **p),
    lambda c, i: c.get_lead(i), (), _LEAD_RETIRE,
    {**_crud(lambda c, cid, b: c.create_lead(cid, body=b),
             lambda c, i, b: c.update_lead(i, body=b), lambda c, i, b: c.delete_lead(i),
             _IN_LEAD, ("first_name", "last_name")),
     "convert": Write(lambda c, i, b: c.convert_lead_to_patient(i), key="patient",
                      shape=_shape(_PATIENT), withheld=_WITHHELD)})}

_SETTINGS = {
    "feature": Kind(
        "features", _shape(_FEATURE), lambda c, cid, **p: c.list_clinic_features(cid, **p)),
    "payment_medium": Kind(
        "payment_mediums", _shape(_NAMED),
        lambda c, cid, **p: c.list_payment_mediums(cid, **p),
        lambda c, i: c.get_payment_medium(i),
        writes=_crud(lambda c, cid, b: c.create_payment_medium(cid, body=b),
                     lambda c, i, b: c.update_payment_medium(i, body=b),
                     lambda c, i, b: c.delete_payment_medium(i), _IN_PAYMENT_MEDIUM,
                     ("name",))),
    "object_label": Kind(
        "object_labels", _shape(_LABEL),
        lambda c, cid, label_types, **p: c.list_object_labels(cid, types=label_types, **p),
        None, ("label_types",)),
    "communication_template": Kind(
        "communication_templates", _shape(_COMMUNICATION_TEMPLATE),
        lambda c, cid, template_kind, **p: c.list_communication_templates(
            cid, kind=template_kind, **p),
        lambda c, i: c.get_communication_template(i), ("template_kind",),
        writes={"update": Write(
            lambda c, i, b: c.update_communication_template(i, body=b), "item",
            _IN_COMMUNICATION_TEMPLATE, ("template",))}),
    "document_template": Kind(
        "document_templates", _shape(_DOCUMENT_TEMPLATE),
        lambda c, cid, document_type, master_template_id, **p: c.list_document_templates(
            cid, type=document_type, master_id=master_template_id, **p),
        lambda c, i: c.get_document_template(i), ("document_type", "master_template_id"),
        writes={**_crud(lambda c, cid, b: c.create_document_template(cid, body=b),
                        lambda c, i, b: c.update_document_template(i, body=b),
                        lambda c, i, b: c.delete_document_template(i),
                        _IN_DOCUMENT_TEMPLATE_CREATE, ("name", "template", "type"),
                        accepted_update=_IN_DOCUMENT_TEMPLATE_UPDATE,
                        required_update=("name", "template")),
                "duplicate": Write(lambda c, i, b: c.duplicate_document_template(i))}),
    "survey_form": Kind(
        "survey_forms", _shape(_SURVEY_FORM),
        lambda c, cid, search, survey_type, **p: c.list_survey_forms(
            cid, search=search, type=survey_type, **p),
        lambda c, i: c.get_survey_form(i), ("search", "survey_type"),
        writes=_crud(lambda c, cid, b: c.create_survey_form(cid, body=b),
                     lambda c, i, b: c.update_survey_form(i, body=b),
                     lambda c, i, b: c.delete_survey_form(i), _IN_SURVEY_FORM_CREATE,
                     ("type", "name", "fields_tmpl"),
                     accepted_update=_IN_SURVEY_FORM_UPDATE, required_update=())),
    "webhook": Kind(
        "webhooks", _shape(_WEBHOOK), lambda c, cid, **p: c.list_webhooks(cid, **p),
        lambda c, i: c.get_webhook(i),
        writes=_crud(lambda c, cid, b: c.create_webhook(cid, body=b),
                     lambda c, i, b: c.update_webhook(i, body=b),
                     lambda c, i, b: c.delete_webhook(i), _IN_WEBHOOK_CREATE,
                     ("action_type", "url"), accepted_update=_IN_WEBHOOK_UPDATE,
                     required_update=("url",))),
}

#: `op="placeholders"`: each kind's type parameter, and its read.
_PLACEHOLDER_READS = {
    "communication_template": (
        "communication_type",
        lambda c, t: c.list_communication_template_placeholders(type=t)),
    "document_template": (
        "document_type", lambda c, t: c.list_document_template_placeholders(type=t)),
    "survey_form": ("survey_type", lambda c, t: c.list_survey_form_placeholders(type=t)),
}
_placeholders = _shape(_PLACEHOLDERS)

_COMMUNICATIONS = {
    "call": Kind(
        "calls", _shape(_CALL),
        withheld=("call served without the number, notes, transcript, summary or "
                  "recording; patient served by id alone."),
        writes={"create": Write(lambda c, cid, b: c.create_call(cid, body=b), "clinic",
                                _IN_CALL)}),
    "message": Kind(
        "messages", _shape(_COMMUNICATION_RECORD),
        withheld="message served without its recipient or its object.",
        writes={"create": Write(
            lambda c, cid, b: c.create_communication_record(cid, body=b), "clinic",
            _IN_COMMUNICATION_RECORD,
            ("communication_template_kind", "communication_template_type", "object"))}),
}

_LABEL_TYPES = Literal["patient", "call", "quote_tag", "quote_channel", "lead_status",
                       "lead_source", "lead_channel", "lead_desired_treatment", "lead_zone"]


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_lead(
        op: Literal["list", "get", "create", "update", "delete", "convert"] = "list",
        clinic_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Leads (prospects) of a Nextmotion clinic — contact identity (first and last
        name, email, phone) and the sales pipeline: source, status, channel, desired
        treatment and zone (clinic labels), follow-ups, scheduled appointment,
        assigned practitioner, done flag. Notes and external reference are written,
        never served back. Searching leads by name is not offered.

        `op`: "list" (default, `clinic_id`) | "get" (`lead_id`) | "create" (`clinic_id` +
        `data`) | "update" (`lead_id` + `data`) — `first_name`, `last_name` required;
        labels are ids from `nextmotion_setting(kind="object_label")` | "delete" |
        "convert" (`lead_id`: becomes a patient, answered by id —
        `nextmotion_patient(op="get")` reads it). `data` takes the fields the Nextmotion
        spec accepts for the op; any other field is refused.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act.

        Args:
            op: list (default) | get | create | update | delete | convert.
            clinic_id: op="list"/"create".
            lead_id: every op but list/create.
            data: op="create"/"update" — the lead's fields.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        return _serve(_LEADS, "lead", op, client=_client, clinic_id=clinic_id,
                      item_id=lead_id, filters={}, limit=limit, offset=offset,
                      fields=fields, data=data, dry_run=dry_run, item_name="lead_id")

    @mcp.tool()
    def nextmotion_setting(
        kind: Literal["feature", "payment_medium", "object_label", "communication_template",
                      "document_template", "survey_form", "webhook"],
        op: Literal["list", "get", "create", "update", "delete", "duplicate",
                    "placeholders"] = "list",
        clinic_id: Optional[str] = None,
        item_id: Optional[str] = None,
        label_types: Optional[list[_LABEL_TYPES]] = None,
        template_kind: Optional[Literal["email", "sms", "whatsapp"]] = None,
        document_type: Optional[Literal[0, 1, 2, 3, 4, 6, 7, 8]] = None,
        master_template_id: Optional[str] = None,
        search: Optional[str] = None,
        survey_type: Optional[Literal["bolt_note", "treatment_consent"]] = None,
        communication_type: Optional[str] = None,
        data: Optional[dict] = None,
        dry_run: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Settings and referentials of a Nextmotion clinic — read and write.

        `kind`:
        - "feature" — Nextmotion subscription options; list only.
        - "payment_medium" — custom payment means.
        - "object_label" — lead, quote, call and patient labels; filter `label_types`;
          list only.
        - "communication_template" — email / SMS / WhatsApp templates (metadata);
          filter `template_kind`; update only (`template` required).
        - "document_template" — document templates (metadata); filters `document_type`
          (0 PRESCRIPTION, 1 QUOTE, 2 INVOICE, 3 DEPOSIT_INVOICE, 4 CREDIT_NOTE,
          6 IMAGE_RIGHTS_CONSENT, 7 ADMINISTRATIVE, 8 VISIT), `master_template_id`;
          + "duplicate".
        - "survey_form" — survey-form TEMPLATES (BoltNote notes, treatment consents),
          never a patient's answers; filters `search`, `survey_type`.
        - "webhook" — action type and URL; `headers` are accepted, NEVER returned.
        Bodies of templates and forms are written whole, read as metadata.

        `op`: "list" (default, `clinic_id`) | "get" (`item_id`) | "create" (`clinic_id` +
        `data`) | "update" (`item_id` + `data`) | "delete" / "duplicate" (`item_id`) |
        "placeholders" — merge fields: communication_template (`communication_type`),
        document_template (`document_type`), survey_form (`survey_type`, required).
        `data` takes the fields the Nextmotion spec accepts for the op; any other field is
        refused. Each filter belongs to its kind only.

        ⚠️ Writes DEFAULT to `dry_run=True`: `data` is checked, the current object and
        what would be sent are returned, nothing is written. `dry_run=False` to act. Webhook
        headers are masked in the preview.

        Args:
            kind: which setting.
            op: see above (default "list").
            clinic_id: op="list"/"create".
            item_id: op="get"/"update"/"delete"/"duplicate".
            label_types / template_kind / document_type / master_template_id / search /
                survey_type: list filters (see above).
            communication_type / document_type / survey_type: op="placeholders".
            data: op="create"/"update" — the fields to send.
            dry_run: writes — default True.
            limit / offset: op="list" — pagination (limit 1..100, default 50).
            fields: op="list" — keep only these keys per row (`id` kept)."""
        filters = {"label_types": label_types, "template_kind": template_kind,
                   "document_type": document_type, "master_template_id": master_template_id,
                   "search": search, "survey_type": survey_type,
                   "communication_type": communication_type}
        if op != "placeholders":
            return _serve(_SETTINGS, kind, op, client=_client, clinic_id=clinic_id,
                          item_id=item_id, filters=filters, limit=limit, offset=offset,
                          fields=fields, data=data, dry_run=dry_run)
        if kind not in _PLACEHOLDER_READS:
            raise _bad("op='placeholders' only applies to kind='communication_template', "
                       "'document_template' or 'survey_form'.")
        type_name, read = _PLACEHOLDER_READS[kind]
        _refuse_ignored(op, clinic_id=clinic_id, item_id=item_id, data=data,
                        dry_run=dry_run, limit=limit, offset=offset, fields=fields,
                        **{n: v for n, v in filters.items() if n != type_name})
        if kind == "survey_form":
            _need(op, survey_type=survey_type)
        c = _client()
        env = _run(lambda: read(c, filters[type_name]))
        data_ = env.get("data") if isinstance(env, dict) else None
        rows = data_ if isinstance(data_, list) else [data_] if data_ is not None else []
        return {"kind": kind, "placeholders": [_placeholders(r) for r in rows]}

    @mcp.tool()
    def nextmotion_communication(
        kind: Literal["call", "message"],
        clinic_id: str,
        data: dict,
        op: Literal["create"] = "create",
        dry_run: Optional[bool] = None,
    ) -> dict:
        """Log a call or SEND a message from a Nextmotion clinic — write only.

        `kind`:
        - "call" — logs a call (`patient` id, `phone_number`, `time`, `direction`,
          `duration`, `status` label, `notes`, `summary`, `transcript`…). Served back
          without the number, notes, transcript, summary or recording.
        - "message" — ⚠️ SENDS an email, SMS or WhatsApp to the patient from a clinic
          template: `communication_template_kind` (email | sms | whatsapp),
          `communication_template_type` (quote | quote_checkout | quote_info_documents |
          invoice | administrative_document — never a medical document), `object` (that
          document's id) required.
        `data` takes the fields the Nextmotion spec accepts for the op; any other field is refused.

        ⚠️ `dry_run` DEFAULTS TO TRUE: `data` is checked, what would be sent is returned,
        nothing is logged or sent. `dry_run=False` to act.

        Args:
            kind: call | message.
            clinic_id: the clinic.
            data: the fields to send.
            op: create (the only op).
            dry_run: default True."""
        if kind not in _COMMUNICATIONS:
            raise _bad(f"unknown kind: {kind!r}.")
        if kind == "message" and isinstance(data, dict):
            kind_type = data.get("communication_template_type")
            if kind_type is not None and kind_type not in _COMMUNICATION_TYPES:
                raise _bad(f"communication_template_type={kind_type!r} is not served: "
                           f"{', '.join(_COMMUNICATION_TYPES)} only (no medical "
                           "document or free patient text).")
        return _serve_write(_COMMUNICATIONS, kind, op, client=_client, clinic_id=clinic_id,
                            item_id=None, data=data, dry_run=dry_run, unused={})
