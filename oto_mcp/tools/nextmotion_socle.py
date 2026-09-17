"""Nextmotion — la projection en LISTE BLANCHE de tout ce qui sort du connecteur.

Séparée des modules d'outils : c'est la seule partie qui décide ce qui SORT d'une
ressource, donc la partie à relire quand l'API change. Chaque ressource a sa liste,
écrite d'après la spec (lue le 2026-09-17) : un champ absent ne passe pas, y compris
un champ que l'API ajouterait demain. Le pourquoi (données de santé, textes libres)
est dans la docstring de `nextmotion.py`.

Une liste est un tuple de champs : un NOM laisse passer une valeur feuille ; une paire
`(nom, sous_liste)` descend dans un objet ou une liste d'objets. **Une feuille ne
transporte jamais d'objet** : si l'API y met un dict, il ne passe pas, et d'une liste
ne passent que les scalaires — un champ non typé par la spec ne devient pas une
échappatoire.

Règles qui valent partout :
- **le patient n'est servi que par son `id`** (`_PATIENT`) ; la personne d'une demande
  de rendez-vous en ligne et celle d'un lead ne sont pas servies du tout (nom, email,
  téléphone, date de naissance, sexe) ;
- **aucun texte libre sur un patient** : `notes`, `free_text`, `details` d'une ligne de
  devis/facture, `rebate_details`, titres, sous-titres et notes d'un évènement d'agenda,
  textes des SMS/WhatsApp de rappel, `pre_payment_message` ;
- **aucun fichier** (photo, document PDF d'un devis ou d'une facture, pièce d'info) ;
- **rien de médical par ricochet** : le lien d'une ligne vers le soin réalisé
  (`treatment`), la consultation d'un parcours, la visite d'un rendez-vous, les
  questionnaires (`bolt_note`, `survey_form`).

⚠️ Ce qui reste en texte : les libellés du CATALOGUE (type de visite, nom d'une ligne,
d'un sous-tarif, `details` d'un tarif du catalogue), les noms et coordonnées
PROFESSIONNELLES des praticiens, les étiquettes (source, statut, soin souhaité, zone
d'un lead). Ils décrivent la prestation, le soignant ou le pipeline, pas le patient ; la
spec ne dit pas si un libellé de ligne est éditable à la main, donc un nom saisi là par
un praticien passerait — risque résiduel assumé, pas un oubli.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .. import output_projection

_WITHHELD = ("patient anonymisé : servi par son seul id, sans nom ni coordonnées, et "
             "rien ne permet d'en retrouver l'identité ; données de santé et textes "
             "libres retirés.")
_TEXTES = "titres, notes et textes libres retirés."
_PERSONNE = ("personne anonymisée : ni nom, ni email, ni téléphone, ni date de "
             "naissance ; notes retirées.")

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
# Ni la date d'ouverture du dossier, ni l'activité photo : c'est la vie du dossier médical.
_PATIENT_STATS = ("first_visit_time", "last_visit_time", "review_request_count",
                  "review_click_count", "quoted_total", "invoiced_total", "paid_total",
                  "credit_note_total", "reimbursments_total", "reimbursed_total")
_LEAD = _T + (("source", _LABEL), "is_done", ("desired_treatment", _LABEL),
              ("treatment_zone", _LABEL), ("status", _LABEL), "last_contact_time",
              ("last_channel_used", _LABEL), "response_received", "response_time",
              "scheduled_appointment_time", ("assigned_doctor", _DOCTOR_NAME),
              "follow_up_count", "messages_sent_count", "nurturing_time")
_NAMED = _T + ("name",)
# Métadonnées seules : le corps d'un gabarit est un objet libre, non décrit par la spec.
_COMMUNICATION_TEMPLATE = _T + ("kind", "type", "is_enabled", "is_empty",
                                "sendgrid_template_id", "brevo_template_id")
_DOCUMENT_TEMPLATE = _T + ("has_source", ("master", ("id", "name")),
                           ("doctor", _DOCTOR_NAME), "type", "name", "has_patient_sign",
                           "has_autocomplete_template", "has_template_text",
                           "display_in_consultations", "autoshow", "is_default", "has_slave")
# Sans `headers` : ils portent d'ordinaire le secret du destinataire.
_WEBHOOK = _T + ("clinic_id", "action_type", "url")


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
    """La fonction de projection d'une liste blanche (une ressource de premier niveau)."""
    def shape(obj: Any) -> Any:
        return _project(obj, spec) if isinstance(obj, dict) else obj
    return shape


_appointment = _shape(_APPOINTMENT)
_quote = _shape(_QUOTE)
_invoice = _shape(_INVOICE)
_product = _shape(_PRODUCT)


def _page(env: Any, key: str, shape=None, fields: Optional[list] = None,
          withheld: Optional[str] = _WITHHELD) -> dict:
    """Une page de liste. `shape` = la liste blanche de la ressource ; `fields` = les
    colonnes que l'appelant garde (l'`id` toujours).

    ⚠️ `fields` s'applique APRÈS la liste blanche et ne peut que retirer : `["*"]`
    rend la vue par défaut, jamais le brut de l'amont (aucune échappatoire vers les
    données de santé)."""
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
