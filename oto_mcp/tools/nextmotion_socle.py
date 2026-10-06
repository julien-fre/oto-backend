"""Nextmotion — the ALLOWLIST projection of everything that leaves the connector.

Separate from the tool modules: this is the only part that decides what COMES OUT of a
resource, hence the part to re-read when the API changes. Each resource has its list,
written from the spec (read on 2026-09-17): a missing field does not pass, including
a field the API would add tomorrow. The why (health data, free texts)
is in the docstring of `nextmotion.py`.

A list is a tuple of fields: a NAME lets a leaf value through; a pair
`(name, sublist)` descends into an object or a list of objects. **A leaf never
carries an object**: if the API puts a dict there, it does not pass, and from a list
only scalars pass — a field not typed by the spec does not become an
escape hatch.

Rules that hold everywhere:
- **outside the patient tool, the patient is served only by their `id`** (`_PATIENT`);
  their identity is read through `nextmotion_patient` (`_PATIENT_IDENTITY`: name, contact details,
  date of birth, age, gender, address, contact consents, number, archived),
  never the practitioner's comments, the photo or the GPS coordinates. A lead serves
  its contact identity (last name, first name, email, phone), never its notes or its
  external reference; the person behind an online appointment request is not
  served at all (name, email, phone, date of birth, sex);
- **no free text about a patient**: `notes`, `free_text`, `details` of a
  quote/invoice line, `rebate_details`, titles, subtitles and notes of a calendar event,
  texts of the SMS/WhatsApp reminders, `pre_payment_message`;
- **no file** (photo, PDF document of a quote or invoice, info document);
- **nothing medical by ricochet**: the link from a line to the treatment performed
  (`treatment`), the consultation of a journey, the visit of an appointment, the
  questionnaires (`bolt_note`, `survey_form`).

⚠️ What remains as text: the CATALOGUE labels (visit type, line name,
sub-pricing name, `details` of a catalogue pricing), the practitioners' PROFESSIONAL
names and contact details, the labels (source, status, desired treatment, zone
of a lead). They describe the service, the caregiver or the pipeline, not the patient; the
spec does not say whether a line label can be hand-edited, so a name typed there by
a practitioner would pass — accepted residual risk, not an oversight.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .. import output_projection

_WITHHELD = ("patient served by their id only, without name or contact details — their identity is "
             "read through nextmotion_patient(op='get'); health data and free texts "
             "removed.")
_IDENTITE = ("patient identity only: practitioner comments, photo, GPS coordinates "
             "and medical file removed.")
_TEXTES = "titles, notes and free texts removed."
_PERSONNE = ("anonymised person: no name, no email, no phone, no date of "
             "birth; notes removed.")
_LEAD_RETIRE = "lead notes, free texts and external reference removed."

_T = ("id", "created_time", "modified_time")
_PATIENT = ("id",)
_LABEL = _T + ("type", "name", "color")
_DOCTOR_NAME = ("id", "prefixed_name")
_RESOURCE = ("id", "name", "color")
_CATEGORY = _T + ("name", "speciality", "position")
_BASIC_VISIT_TYPE = _T + (("category", _CATEGORY), "subject", "duration_minutes", "price",
                          "color", "has_provider_sign", "has_quickfill_note_tmpl",
                          "journey_steps_order", "has_bolt_note")
_BASIC_SUB_VISIT_TYPE = _T + ("color", "subject", "duration_minutes", "has_bolt_note",
                              "has_bolt_note_fields_tmpl", "price", "display_in_agenda",
                              "display_in_dashboard")
_PRESET = (("treatment_pricing", ("id", "price", "details")), "quantity")
_VISIT_FLAGS = ("display_in_agenda", "display_in_dashboard", "display_in_patient_file",
                "display_in_appointment_form", "next_visit_reminder_days",
                "enable_treatment_pre_payments", "has_provider_sign",
                ("appointment_treatment_presets", _PRESET))
_SUB_VISIT_TYPE = _BASIC_SUB_VISIT_TYPE + _VISIT_FLAGS + (("visit_type", _BASIC_VISIT_TYPE),)
_VISIT_TYPE = _BASIC_VISIT_TYPE + ("clinic_chain_id", "position") + _VISIT_FLAGS + (
    ("sub_visit_types", _SUB_VISIT_TYPE),)
_ACCOUNTING_DISTRIBUTION = _T + ("name", ("model", ("price_percent", "vat_rate", "details")))
_SUBPRICING = ("kind", "name", "price", "rebate", "markup", "vat_rate", "vat_rate_frac",
               "distributed_to", "accounting_code")
_GLOBAL_PRODUCT = _T + ("name", "brand", "image")
_PRICING = _T + ("position", "price", "rebate", "details", "vat_rate", "vat_rate_frac",
                 ("subpricing", _SUBPRICING + ("details",)), "sessions", "accounting_code",
                 ("products", _T + (("global_product", _GLOBAL_PRODUCT), "quantity")),
                 "appointment_visit_type", "appointment_sub_visit_type",
                 ("accounting_distribution", _ACCOUNTING_DISTRIBUTION), "distributed_to",
                 "next_treatment_reminder_days", "duplicate_on_invoice_paid",
                 "send_reminder_on_invoice_paid", "has_info",
                 "require_products_before_invoice_create")
_TREATMENT_TYPE = _T + ("clinic_chain_id", "name", "position", ("pricings", _PRICING),
                        "mould_id", ("visit_type", _BASIC_VISIT_TYPE),
                        ("sub_visit_type", _BASIC_SUB_VISIT_TYPE), "has_consent_form_tmpl",
                        "display_in_dashboard", "create_visit",
                        ("accounting_distribution", _ACCOUNTING_DISTRIBUTION))
_PACKAGE = _T + ("name", "price", "vat_rate",
                 ("accounting_distribution", _ACCOUNTING_DISTRIBUTION), "distributed_to")
_PACKAGE_ITEM = _T + (("type", _TREATMENT_TYPE), ("pricing", _PRICING), "sessions")
_USER_DISTRIBUTION = ("user", ("accounting_distribution", _ACCOUNTING_DISTRIBUTION))

_CLINIC = _T + ("name", "logo", "preview_logo", "domain", "phone_number", "street_address",
                "zip_code", "city", "country", "latitude", "longitude", "timezone",
                "preferred_language")
_CLINIC_INFO = ("id", "name", "vat_number", "street_address", "zip_code", "city",
                "phone_number", "country", "timezone", "review", "default_currency_code",
                "can_access_marketplace", "can_access_doctorlib", "can_access_stellair",
                "can_access_sadmin_documents", "can_access_nabla",
                "can_access_jarvis_assistant")
_COLLAB = _T + ("user_id", "email", "first_name", "last_name", "preferred_language",
                "speciality", "kind", "color", ("clinic", _CLINIC_INFO), "permissions",
                "id_no_1", "id_no_2", "id_no_3", ("visit_type_category", _CATEGORY),
                "visit_types", "display_in_calendar_provider_view",
                "activate_online_booking", "display_in_appointment_page",
                "display_in_patient_ownership_dropdown", "has_copilot_ai_access",
                "is_active", "is_disabled", "is_admin", "is_assistant")
_FEATURE = _T + ("code", "name", "description", "total_price", "price", "paid_price",
                  "admin_price", "payment_method", "paid_payment_method",
                  "admin_payment_method", "count", "paid_count", "admin_count",
                  "package_count", "package_code", "package_payment_method", "sale_time",
                  "paid_sale_time", "admin_sale_time", "package_sale_time", "cancel_time",
                  "paid_cancel_time", "admin_cancel_time", "package_cancel_time",
                  "end_time", "paid_end_time", "admin_end_time", "package_end_time",
                  "used_count", "remaining_count", "is_enabled")

_EVENT = _T + ("type", ("doctors", _DOCTOR_NAME + ("color",)),
               ("appointment_rooms", _RESOURCE), ("appointment_devices", _RESOURCE),
               "resource_ids", "color", "initial_start_time", "start_time",
               "start_time_utc_offset", "start_time_utc_offset_seconds", "initial_end_time",
               "end_time", "end_time_utc_offset", "end_time_utc_offset_seconds",
               "duration_minutes", "recurrence", "custom_recurrence_step",
               "custom_recurrence_interval", "custom_recurrence_week_days",
               "custom_recurrence_month_by_day_number", "custom_recurrence_end_date",
               "appointment_room_id")
_APPOINTMENT = _T + (("request", _T + ("status",)), ("visit_type", _BASIC_VISIT_TYPE),
                     ("sub_visit_type", _BASIC_SUB_VISIT_TYPE), ("patient", _PATIENT),
                     ("room", _RESOURCE), ("device", _RESOURCE), "status", "statuses",
                     ("calendar_event", _EVENT))
_APPOINTMENT_REQUEST = _T + (
    ("visit_type", _BASIC_VISIT_TYPE), ("sub_visit_type", _BASIC_SUB_VISIT_TYPE),
    ("doctor", _T + ("first_name", "last_name", "prefixed_name", "kind", "is_disabled")),
    "appointment_room_id", "start_time", "start_time_utc_offset",
    "start_time_utc_offset_seconds", "end_time", "end_time_utc_offset",
    "end_time_utc_offset_seconds")
_JOURNEY = _T + (("patient", _PATIENT), ("visit_type", _BASIC_VISIT_TYPE),
                 ("sub_visit_type", _BASIC_SUB_VISIT_TYPE), ("appointment_room", _RESOURCE),
                 "required_steps", "completed_steps", ("calendar_event", _EVENT))
_ROOM = _T + ("name", "position", "activate_online_booking", "color", "visit_type_category",
              "visit_type_categories", "visit_types", "sub_visit_types")
_DEVICE = _T + ("name", "visit_types", "sub_visit_types", "certificate_document_count",
                "regular_document_count", "note_count", "availability")
_OPENING_HOUR = _T + ("requires_available_doctor_and_room", "slots_count",
                      "are_slots_unique", "visit_type_categories", "visit_types",
                      "sub_visit_types", ("calendar_event", _EVENT))
_ABSENCE = _T + (("calendar_event", _EVENT),)

_LINE = _T + ("position", "name", "price", "quantity", "rebate", "rebate_percent", "markup",
              ("subpricing", _SUBPRICING), "vat_rate", "vat_price", "vat_excl_price")
_BILLING = _T + ("issued_time", "number", "number_id", "status", ("patient", _PATIENT),
                 "rebate", "rebate_percent", "rebate_vat_rate", "sub_total_vat_excl_price",
                 "vat_price", "sub_total_vat_incl_price", "total_net_price",
                 "total_vat_price", "total_price")
_QUOTE = _BILLING + ("action_time", "send_time", "last_contact_time",
                     ("last_channel_used", _LABEL), "follow_up_count",
                     "last_follow_up_time", "next_follow_up_time", "response_received",
                     "response_time", "scheduled_appointment_time",
                     ("quoted_treatments", _LINE))
_INVOICE = _BILLING + ("overridden_created_time", "invoiced_time", "payment_methods",
                       "ref_quote_id", ("invoiced_treatments", _LINE))
_PAYMENT = _T + (("invoice", _INVOICE), "check", "cash", "card", "transfer", "stripe",
                 "voucher", "other", ("custom_medium_list", ("id", "name", "amount")),
                 "deferred")
_PRODUCT = _T + ("lot_number", "expiration_date", "stock_level", "warning_level",
                 "physical_stock_level", "physical_stock_diff", "unit_price",
                 ("global_product", _GLOBAL_PRODUCT))
_CHART = ("title", "labels", ("datasets", ("data", "color")))
# Neither the file's opening date nor the photo activity: that is the life of the medical file.
_PATIENT_STATS = ("first_visit_time", "last_visit_time", "review_request_count",
                  "review_click_count", "quoted_total", "invoiced_total", "paid_total",
                  "credit_note_total", "reimbursments_total", "reimbursed_total")
# The prospect's contact identity is served (decision of 2026-10-01); neither their notes
# nor their external reference (an identifier at a third party).
_LEAD = _T + ("first_name", "last_name", "email", "phone_number", ("source", _LABEL),
              "is_done", ("desired_treatment", _LABEL),
              ("treatment_zone", _LABEL), ("status", _LABEL), "last_contact_time",
              ("last_channel_used", _LABEL), "response_received", "response_time",
              "scheduled_appointment_time", ("assigned_doctor", _DOCTOR_NAME),
              "follow_up_count", "messages_sent_count", "nurturing_time")
_NAMED = _T + ("name",)
# Metadata only: a template's body is a free-form object, not described by the spec.
_COMMUNICATION_TEMPLATE = _T + ("kind", "type", "is_enabled", "is_empty",
                                "sendgrid_template_id", "brevo_template_id")
_DOCUMENT_TEMPLATE = _T + ("has_source", ("master", ("id", "name")),
                           ("doctor", _DOCTOR_NAME), "type", "name", "has_patient_sign",
                           "has_autocomplete_template", "has_template_text",
                           "display_in_consultations", "autoshow", "is_default", "has_slave")
# Without `headers`: they usually carry the recipient's secret.
_WEBHOOK = _T + ("clinic_id", "action_type", "url")
_PLACEHOLDER = ("code", "label", "required")
_PLACEHOLDERS = ("type", ("autocomplete_list", _PLACEHOLDER), ("link_list", _PLACEHOLDER))
# Questionnaire model: metadata and merge fields, without its body (free-form object).
_SURVEY_FORM = _T + ("clinic_chain", "clinic", "type", "name",
                     ("note_tmpl", _PLACEHOLDERS[1:]),
                     ("custom_patient_fields_tmpl", _PLACEHOLDERS[1:]))
# Without the attachment (a file) or the email bodies.
_FOLLOW_UP_EMAIL = ("delay_seconds", "is_enabled", ("survey_form", ("id", "type", "name")))
_POST_TREATMENT_CONFIG = _T + ("deal_lost_after_seconds",
                               ("post_follow_up_email", _FOLLOW_UP_EMAIL),
                               ("reminder_email", _FOLLOW_UP_EMAIL))
# A call: neither the number called, nor the notes, transcript, summary or
# recording — what a person said on the phone.
_CALL = _T + ("source", "time", "time_utc_offset", "time_utc_offset_seconds",
              ("status", _LABEL), "direction", "duration", "is_appointment_made", "is_new",
              ("patient", _PATIENT))
# A sent message: neither its recipient (email, phone) nor its subject.
_COMMUNICATION_RECORD = _T + ("communication_template_kind", "communication_template_type",
                              "object_type", "object_id",
                              ("events", _T + ("source", "type", "error_code", "has_error")))
_CREDIT_NOTE = _T + ("issued_time", "void_time", "invoice", ("patient", _PATIENT),
                     "is_patient_deleted", ("issuer_details", _DOCTOR_NAME), "number_id",
                     "value", "vat_rate", "status", "vat_value", "vat_excl_value",
                     "can_download_document", "allow_cancel")
# The patient's identity, for `nextmotion_patient` ALONE: neither `doctor_comments`, nor
# photograph, nor latitude / longitude.
_PATIENT_IDENTITY = _T + ("first_name", "last_name", "email", "phone_number", "birth_date",
                          "age", "gender", "postal_address", "zip_code", "city", "country",
                          "has_email_contact_consent", "has_phone_contact_consent",
                          "has_sms_contact_consent", "has_post_contact_consent",
                          "patient_number", "is_archived")


def _leaf(value: Any) -> Any:
    if isinstance(value, dict):
        return None
    if isinstance(value, list):
        return [v for v in value if not isinstance(v, (dict, list))]
    return value


def _project(value: Any, spec: tuple) -> Any:
    if isinstance(value, list):
        return [_project(v, spec) for v in value if isinstance(v, dict)]
    if not isinstance(value, dict):
        return None
    out: dict = {}
    for field in spec:
        name, sub = (field, None) if isinstance(field, str) else field
        if name not in value:
            continue
        if sub is None:
            leaf = _leaf(value[name])
            if leaf is not None or value[name] is None:
                out[name] = leaf
        else:
            out[name] = _project(value[name], sub)
    return out


def _shape(spec: tuple) -> Callable[[Any], Any]:
    """The projection function of an allowlist (a top-level resource)."""
    def shape(obj: Any) -> Any:
        return _project(obj, spec) if isinstance(obj, dict) else obj
    return shape


_appointment = _shape(_APPOINTMENT)
_quote = _shape(_QUOTE)
_invoice = _shape(_INVOICE)
_product = _shape(_PRODUCT)


def _page(env: Any, key: str, shape=None, fields: Optional[list] = None,
          withheld: Optional[str] = _WITHHELD) -> dict:
    """A list page. `shape` = the resource's allowlist; `fields` = the
    columns the caller keeps (the `id` always).

    ⚠️ `fields` applies AFTER the allowlist and can only remove: `["*"]`
    returns the default view, never the raw upstream (no escape hatch to
    health data)."""
    env = env if isinstance(env, dict) else {}
    rows = env.get("data") or []
    if shape is not None:
        rows = [shape(r) for r in rows]
    out = {"count": env.get("count"), "has_more": env.get("next") is not None, key: rows}
    if fields is not None and output_projection.RAW not in fields:
        out = output_projection.project(out, items_path=key, fields=set(fields) | {"id"})
    if shape is not None and withheld:
        out["withheld"] = withheld
    return out


def _one(env: Any, key: str, shape=None, withheld: Optional[str] = _WITHHELD) -> dict:
    data = env.get("data") if isinstance(env, dict) else None
    if shape is None:
        return {key: data}
    out = {key: shape(data)}
    if withheld:
        out["withheld"] = withheld
    return out
