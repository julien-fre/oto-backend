"""Nextmotion — the allowlist of everything that goes IN: the fields a write
accepts in `data`, per resource and per op.

Counterpart of `nextmotion_socle` (what comes OUT). Each list is drawn from the spec's
`requestBody` (read on 2026-10-01), `readOnly` fields removed, same shape as the
output lists: a NAME accepts a value, a `(name, sub_list)` pair descends into an object
or a list of objects. **A field absent from a list is REFUSED, by name** — never
ignored (`nextmotion_garde._check_body`): a typo does not go out silently.

Deliberately removed, although the spec accepts them:
- **any link to medical content**: an appointment's visit (`visit`), an event's
  treatment-session status (`treatment_session_status`), the lines of a
  quote or invoice (`treatments`: each REQUIRES the id of a clinical treatment, drawn
  from a consultation), a credit-note line's references to a treatment or an extracted
  package (`treatment_id`, `treatment_package_id`, `treatment_package_extract_id`);
- **on the patient**: the practitioner's comments (`doctor_comments`) — only the
  identity can be written;
- **files**: they only go out as multipart (a call recording, an attachment
  of a follow-up email), which this connector does not send.

A webhook's `headers` usually carry the recipient's secret: accepted on
input, never returned (`_SECRETS`, masked even in the `dry_run` preview).

**Never an implicit notification**: a send flag that the spec sets to `true` by
default goes out as `false` unless explicitly requested (`_NO_NOTIFY_APPOINTMENT`).
"""
from __future__ import annotations

_SECRETS = ("headers",)

# --- calendar -------------------------------------------------------------------------

_EVENT_BASE = ("title", "notes", "color", "doctors", "appointment_rooms",
               "appointment_devices", "start_time", "end_time")
_IN_EVENT = _EVENT_BASE + ("recurrence", "custom_recurrence_step",
                           "custom_recurrence_interval", "custom_recurrence_week_days",
                           "custom_recurrence_month_by_day_number",
                           "custom_recurrence_end_date")
#: `default: true` in the spec: an edit would notify the patient silently.
_NO_NOTIFY_APPOINTMENT = {"send_appointment_modified_email": False,
                          "send_appointment_modified_sms": False}
_IN_APPOINTMENT = (("calendar_event", _EVENT_BASE), "visit_type", "sub_visit_type",
                   "subject", "room", "device", "status", "statuses",
                   "send_appointment_modified_email", "send_appointment_modified_sms",
                   "custom_status")
_IN_APPOINTMENT_REQUEST = ("doctor", "visit_type_opening_hour", "time_slot", "email",
                           "first_name", "last_name", "birth_date", "phone_number",
                           "gender")
_IN_ABSENCE = (("calendar_event", _IN_EVENT),)
_IN_OPENING_HOUR = (("calendar_event", _IN_EVENT), "slots_count", "are_slots_unique",
                    "requires_available_doctor_and_room", "visit_types", "sub_visit_types")
_IN_ROOM = ("name", "position", "activate_online_booking", "color", "visit_types",
            "sub_visit_types", "visit_type_category")
_IN_DEVICE = ("name", "visit_types", "sub_visit_types")

# --- practitioners --------------------------------------------------------------------

_COLLAB_COMMON = ("first_name", "last_name", "email", "speciality", "kind", "color",
                  "display_in_calendar_provider_view", "activate_online_booking",
                  "display_in_appointment_page", "display_in_patient_ownership_dropdown",
                  "has_copilot_ai_access", "id_no_1", "id_no_2", "id_no_3",
                  "visit_type_category", "visit_types")
_IN_DOCTOR_CREATE = _COLLAB_COMMON
_IN_DOCTOR_UPDATE = _COLLAB_COMMON + ("display_in_dashboard",)

# --- catalogue ------------------------------------------------------------------------

_PRESETS = ("appointment_treatment_presets", ("treatment_pricing", "quantity"))
_VISIT_FLAGS = ("display_in_agenda", "display_in_dashboard", "display_in_patient_file",
                "display_in_appointment_form", "next_visit_reminder_days",
                "enable_treatment_pre_payments", "bolt_note", _PRESETS)
_IN_SUB_VISIT_TYPE = ("id", "color", "subject", "duration_minutes", "price") + _VISIT_FLAGS
_IN_VISIT_TYPE_CREATE = ("category", "subject", "duration_minutes", "color", "price",
                         ("sub_visit_types", _IN_SUB_VISIT_TYPE)) + _VISIT_FLAGS
_IN_VISIT_TYPE_UPDATE = _IN_VISIT_TYPE_CREATE + ("journey_steps_order",)
_IN_REORDER = ("id",)
_IN_CATEGORY = ("name", "speciality")
_IN_MODEL = ("model", ("price_percent", "vat_rate", "details"))
_IN_SUBPRICING = ("kind", "name", "price", "rebate", "markup", "vat_rate", "details",
                  "distributed_to", "accounting_code")
_IN_PRICING = ("id", "price", "rebate", "details", "vat_rate",
               ("subpricing", _IN_SUBPRICING), "sessions", "accounting_code",
               ("products", ("global_product", "quantity")), "prescription_types",
               "survey_form", "appointment_visit_type", "appointment_sub_visit_type",
               "accounting_distribution", "distributed_to", "next_treatment_reminder_days",
               "duplicate_on_invoice_paid", "send_reminder_on_invoice_paid",
               "require_products_before_invoice_create")
_IN_TREATMENT_TYPE_UPDATE = ("name", ("pricings", _IN_PRICING), "visit_type",
                             "sub_visit_type", "display_in_dashboard", "create_visit")
_IN_TREATMENT_TYPE_CREATE = ("id",) + _IN_TREATMENT_TYPE_UPDATE
_IN_TEMPLATE = ("template", ("html", "subject"))
_IN_FOLLOW_UP_EMAIL = (("survey_form", ("name", "fields_tmpl", ("note_tmpl", (_IN_TEMPLATE,)))),
                       "delay_seconds", "is_enabled")
_IN_POST_TREATMENT = (("post_follow_up_email", _IN_FOLLOW_UP_EMAIL),
                      ("reminder_email", _IN_FOLLOW_UP_EMAIL), "deal_lost_after_seconds")
_IN_PACKAGE = ("name", "price", "vat_rate", "accounting_distribution", "distributed_to")
_IN_PACKAGE_ITEM = ("pricing", "sessions")
_IN_PACKAGE_ITEMS = ("id",) + _IN_PACKAGE_ITEM
_IN_ACCOUNTING_DISTRIBUTION = ("name", _IN_MODEL)
_IN_USER_DISTRIBUTION = ("user", "accounting_distribution")

# --- sales ----------------------------------------------------------------------------

_AUTOCOMPLETE = ("autocomplete", (("a", ("q_id", "val")),))
_CUSTOM_MEDIUMS = ("custom_medium_list", ("id", "amount"))
_BILLING = (_AUTOCOMPLETE, "document_template", "title", "issued_time", "rebate",
            "rebate_percent", "rebate_details", "free_text")
_IN_QUOTE = _BILLING + ("template_text", "tag", "next_step_date", "send_time",
                        "last_contact_time", "last_channel_used", "follow_up_count",
                        "last_follow_up_time", "next_follow_up_time", "response_received",
                        "response_time", "scheduled_appointment_time")
_IN_QUOTE_VALIDATE = _IN_QUOTE + ("is_ordoclic_certified",)
_IN_INVOICE = _BILLING + ("overridden_created_time", "invoiced_time")
_IN_INVOICE_VALIDATE = ("issued_time", "invoiced_time")
_SUBPAYMENT = ("subpayment", ("check", "cash", "card", "transfer", "voucher_code",
                              "voucher_amount", "other", _CUSTOM_MEDIUMS))
_IN_PAY = ("check", "cash", "card", "transfer", "other", _CUSTOM_MEDIUMS, "deferred",
           _AUTOCOMPLETE, "voucher_code", "voucher_amount", _SUBPAYMENT, "do_validate")
_IN_PAYMENT = ("check", "cash", "card", "transfer", "other", "voucher_code",
               _CUSTOM_MEDIUMS, _SUBPAYMENT, "payment_time", "deferred", _AUTOCOMPLETE)
_IN_CREDIT_NOTE = (
    _AUTOCOMPLETE, "document_template", "patient", "invoice",
    ("items", ("name", "details", "amount",
               ("subamount", ("price", "reimbursment", "subreimbursment", "details")),
               "subpayment_amounts", "vat_rate", "reimbursment", "subreimbursment")),
    ("payments", ("check", "cash", "card", "transfer", "voucher", "other",
                  ("subpayment", ("check", "cash", "card", "transfer", "voucher", "other",
                                  _CUSTOM_MEDIUMS)), _CUSTOM_MEDIUMS)),
    "do_validate", "issued_time")
_IN_PAYMENT_MEDIUM = ("name",)
_IN_PRODUCT_UPDATE = ("lot_number", "expiration_date", "stock_level", "warning_level",
                      "physical_stock_level", "unit_price")
_IN_PRODUCT_CREATE = ("global_product",) + _IN_PRODUCT_UPDATE

# --- CRM and settings -----------------------------------------------------------------

_IN_LEAD = ("first_name", "last_name", "email", "phone_number", "notes", "source",
            "is_done", "desired_treatment", "treatment_zone", "status", "last_contact_time",
            "last_channel_used", "response_received", "response_time",
            "scheduled_appointment_time", "assigned_doctor", "follow_up_count",
            "messages_sent_count", "nurturing_time", "external_reference")
_IN_CALL = ("patient", "phone_number", "time", "status", "notes",
            ("transcript", ("role", "second", "text")), "recording_url", "direction",
            "summary", "transcript_insights", "duration")
_IN_COMMUNICATION_RECORD = ("communication_template_kind", "communication_template_type",
                            "object", _IN_TEMPLATE)
#: The message types a send can carry: commercial and administrative
#: documents. Prescription, consent, a treatment's documents, a patient's free document
#: or text stay outside the connector.
_COMMUNICATION_TYPES = ("quote", "quote_checkout", "quote_info_documents", "invoice",
                        "administrative_document")
_IN_COMMUNICATION_TEMPLATE = ("sendgrid_template_id", "brevo_template_id", "template",
                              "is_enabled")
_DOCUMENT_TEMPLATE = ("master", "doctor", "name", "intro_text", _IN_TEMPLATE,
                      "autocomplete_template", ("template_texts", ("name", "content")),
                      "display_in_consultations", "autoshow")
_IN_DOCUMENT_TEMPLATE_CREATE = _DOCUMENT_TEMPLATE + ("type",)
_IN_DOCUMENT_TEMPLATE_UPDATE = _DOCUMENT_TEMPLATE
_IN_SURVEY_FORM_UPDATE = ("name", "fields_tmpl", ("note_tmpl", (_IN_TEMPLATE,)))
_IN_SURVEY_FORM_CREATE = ("type",) + _IN_SURVEY_FORM_UPDATE
_IN_WEBHOOK_UPDATE = ("url", "target", "headers", "input")
_IN_WEBHOOK_CREATE = ("action_type",) + _IN_WEBHOOK_UPDATE

# --- patient: the identity, without the practitioner's comments -----------------------

_IN_PATIENT = ("email", "first_name", "last_name", "gender", "birth_date",
               "postal_address", "zip_code", "city", "phone_number", "patient_number",
               "doctor", "is_archived", "has_email_contact_consent",
               "has_phone_contact_consent", "has_sms_contact_consent",
               "has_post_contact_consent")
_REQUIRED_PATIENT = ("email", "first_name", "last_name", "gender")
