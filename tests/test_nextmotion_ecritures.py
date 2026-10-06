"""Connecteur Nextmotion — les écritures de l'administratif et l'identité du patient.

Ce que ce fichier verrouille :
- le ROUTAGE de chaque op d'écriture vers la bonne méthode du client, avec la cible
  (clinique ou objet) et le corps tels qu'envoyés ;
- **`dry_run` vaut True par défaut** : sans `dry_run=False`, aucune méthode autre
  qu'une lecture (`get_*`, `list_*`) n'est appelée, et l'aperçu redit ce qui partirait ;
- **`data` passe une liste blanche d'ENTRÉE** : un champ inconnu est refusé nommément,
  à toute profondeur (objet imbriqué, liste d'objets), un champ requis manquant aussi,
  et `data` / `dry_run` sur une lecture sont refusés (`is not None`) ;
- **l'identité du patient** sort par `nextmotion_patient` seul, sans les commentaires
  du praticien, la photo ni les coordonnées GPS ; `doctor_comments` n'entre pas ;
- les `headers` d'un webhook entrent mais ne sortent jamais, aperçu compris ;
- un message ne s'envoie que pour une pièce commerciale ou administrative ;
- **jamais de notification implicite** : la modification d'un rendez-vous part avec
  `send_appointment_modified_email|sms = false` sauf demande explicite ;
- **aucun id de soin clinique n'entre** par une écriture de vente (lignes de devis ou
  de facture, références d'une ligne d'avoir) ;
- l'aperçu valide les ids de chemin (UUID) comme l'écriture le ferait ;
- un lead sert son identité de contact, jamais ses notes ni sa référence externe.

Toutes les valeurs sont factices.
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError

C = "00000000-0000-4000-8000-00000000000c"
X = "00000000-0000-4000-8000-00000000000d"
S = "SENTINELLE-ECRITURE"
EVENT = {"start_time": "2026-01-02T10:00:00+01:00", "end_time": "2026-01-02T11:00:00+01:00"}


def _mcp():
    import importlib

    from fastmcp import FastMCP
    from oto_mcp.providers.nextmotion import CONNECTOR

    m = FastMCP("t")
    for mod in CONNECTOR.modules:
        importlib.import_module(f"oto_mcp.tools.{mod}").register(m)
    return m


def _tool(name):
    return asyncio.run(_mcp().get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.nextmotion.NextmotionClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("nm-test", False))
    return inst


# (outil, arguments SANS dry_run, méthode attendue, args, kwargs envoyés)
_ECRITURES = [
    ("nextmotion_calendar", {"kind": "room", "op": "create", "clinic_id": C,
                             "data": {"name": "Salle"}},
     "create_appointment_room", (C,), {"body": {"name": "Salle"}}),
    ("nextmotion_calendar", {"kind": "device", "op": "update", "item_id": X,
                             "data": {"name": "Laser"}},
     "update_appointment_device", (X,), {"body": {"name": "Laser"}}),
    ("nextmotion_calendar", {"kind": "opening_hour", "op": "delete", "item_id": X},
     "delete_calendar_opening_hour", (X,), {}),
    ("nextmotion_calendar", {"kind": "absence", "op": "create", "clinic_id": C,
                             "data": {"calendar_event": dict(EVENT, doctors=[X])}},
     "create_calendar_absence", (C,),
     {"body": {"calendar_event": dict(EVENT, doctors=[X])}}),
    ("nextmotion_calendar", {"kind": "appointment_request", "op": "create",
                             "data": {"visit_type_opening_hour": X, "time_slot": "t",
                                      "email": "e", "first_name": "f", "last_name": "l",
                                      "birth_date": "2000-01-01", "phone_number": "p"}},
     "create_appointment_request", (),
     {"body": {"visit_type_opening_hour": X, "time_slot": "t", "email": "e",
               "first_name": "f", "last_name": "l", "birth_date": "2000-01-01",
               "phone_number": "p"}}),
    ("nextmotion_appointment", {"op": "update", "appointment_id": X,
                                "data": {"calendar_event": EVENT, "status": "confirmed"}},
     "update_appointment", (X,),
     {"body": {"calendar_event": EVENT, "status": "confirmed",
               "send_appointment_modified_email": False,
               "send_appointment_modified_sms": False}}),
    ("nextmotion_practitioner", {"op": "create", "clinic_id": C,
                                 "data": {"email": "e", "kind": 5}},
     "create_doctor", (C,), {"body": {"email": "e", "kind": 5}}),
    ("nextmotion_practitioner", {"op": "update", "doctor_id": X,
                                 "data": {"speciality": ["s"]}},
     "update_doctor", (X,), {"body": {"speciality": ["s"]}}),
    ("nextmotion_practitioner", {"op": "delete", "doctor_id": X}, "delete_doctor", (X,), {}),
    ("nextmotion_product", {"op": "create", "clinic_id": C, "data": {"global_product": X}},
     "create_product", (C,), {"body": {"global_product": X}}),
    ("nextmotion_product", {"op": "update", "product_id": X, "data": {"stock_level": "1"}},
     "update_product", (X,), {"body": {"stock_level": "1"}}),
    ("nextmotion_product", {"op": "delete", "product_id": X}, "delete_product", (X,), {}),
    ("nextmotion_catalog", {"kind": "visit_type", "op": "create", "clinic_id": C,
                            "data": {"subject": "Visite", "color": "FEC0CB"}},
     "create_visit_type", (C,), {"body": {"subject": "Visite", "color": "FEC0CB"}}),
    ("nextmotion_catalog", {"kind": "visit_type", "op": "reorder", "clinic_id": C,
                            "data": [{"id": X}]},
     "reorder_visit_types", (C,), {"items": [{"id": X}]}),
    ("nextmotion_catalog", {"kind": "visit_type_category", "op": "reorder",
                            "clinic_id": C, "data": [{"id": X}]},
     "reorder_visit_type_categories", (C,), {"items": [{"id": X}]}),
    ("nextmotion_catalog", {"kind": "treatment_type", "op": "update", "item_id": X,
                            "data": {"name": "Soin", "pricings": [{"price": "1.00"}]}},
     "update_treatment_type", (X,), {"body": {"name": "Soin", "pricings": [{"price": "1.00"}]}}),
    ("nextmotion_catalog", {"kind": "treatment_type", "op": "update_post_treatment",
                            "item_id": X, "data": {"deal_lost_after_seconds": 60}},
     "update_post_treatment_config", (X,), {"body": {"deal_lost_after_seconds": 60}}),
    ("nextmotion_catalog", {"kind": "treatment_package", "op": "add_item", "item_id": X,
                            "data": {"pricing": C}},
     "create_treatment_package_item", (X,), {"body": {"pricing": C}}),
    ("nextmotion_catalog", {"kind": "treatment_package", "op": "set_items", "item_id": X,
                            "data": [{"pricing": C, "sessions": 2}]},
     "replace_treatment_package_items", (X,), {"items": [{"pricing": C, "sessions": 2}]}),
    ("nextmotion_catalog", {"kind": "treatment_pricing", "op": "set_distributions",
                            "item_id": X, "data": [{"user": C, "accounting_distribution": X}]},
     "set_treatment_pricing_distributions", (X,),
     {"items": [{"user": C, "accounting_distribution": X}]}),
    ("nextmotion_catalog", {"kind": "treatment_package_item", "op": "delete", "item_id": X},
     "delete_treatment_package_item", (X,), {}),
    ("nextmotion_catalog", {"kind": "accounting_distribution", "op": "create",
                            "clinic_id": C,
                            "data": {"name": "R", "model": [{"price_percent": 50}]}},
     "create_accounting_distribution", (C,),
     {"body": {"name": "R", "model": [{"price_percent": 50}]}}),
    ("nextmotion_quote", {"op": "update", "quote_id": X, "data": {"rebate": "1.00"}},
     "update_quote", (X,), {"body": {"rebate": "1.00"}}),
    ("nextmotion_quote", {"op": "delete", "quote_id": X}, "delete_quote", (X,), {}),
    ("nextmotion_quote", {"op": "validate", "quote_id": X}, "validate_quote", (X,),
     {"body": None}),
    ("nextmotion_invoice", {"op": "update", "invoice_id": X,
                            "data": {"rebate_percent": "10"}},
     "update_invoice", (X,), {"body": {"rebate_percent": "10"}}),
    ("nextmotion_invoice", {"op": "validate", "invoice_id": X,
                            "data": {"invoiced_time": "2026-01-02T10:00:00Z"}},
     "validate_invoice", (X,), {"body": {"invoiced_time": "2026-01-02T10:00:00Z"}}),
    ("nextmotion_invoice", {"op": "pay", "invoice_id": X, "data": {"card": "10.00"}},
     "pay_invoice", (X,), {"body": {"card": "10.00"}}),
    ("nextmotion_invoice", {"op": "credit_note", "clinic_id": C,
                            "data": {"patient": X, "items": [{"amount": "1.00"}]}},
     "create_credit_note", (C,), {"body": {"patient": X, "items": [{"amount": "1.00"}]}}),
    ("nextmotion_payment", {"op": "update", "payment_id": X, "data": {"cash": "1.00"}},
     "update_payment", (X,), {"body": {"cash": "1.00"}}),
    ("nextmotion_lead", {"op": "create", "clinic_id": C,
                         "data": {"first_name": "f", "last_name": "l", "source": X}},
     "create_lead", (C,), {"body": {"first_name": "f", "last_name": "l", "source": X}}),
    ("nextmotion_lead", {"op": "update", "lead_id": X,
                         "data": {"first_name": "f", "last_name": "l", "is_done": True}},
     "update_lead", (X,), {"body": {"first_name": "f", "last_name": "l", "is_done": True}}),
    ("nextmotion_lead", {"op": "delete", "lead_id": X}, "delete_lead", (X,), {}),
    ("nextmotion_lead", {"op": "convert", "lead_id": X}, "convert_lead_to_patient", (X,), {}),
    ("nextmotion_setting", {"kind": "payment_medium", "op": "create", "clinic_id": C,
                            "data": {"name": "Bon"}},
     "create_payment_medium", (C,), {"body": {"name": "Bon"}}),
    ("nextmotion_setting", {"kind": "communication_template", "op": "update", "item_id": X,
                            "data": {"template": {"html": "<p/>"}, "is_enabled": True}},
     "update_communication_template", (X,),
     {"body": {"template": {"html": "<p/>"}, "is_enabled": True}}),
    ("nextmotion_setting", {"kind": "document_template", "op": "create", "clinic_id": C,
                            "data": {"name": "D", "type": 1,
                                     "template": {"html": "<p/>", "subject": "s"}}},
     "create_document_template", (C,),
     {"body": {"name": "D", "type": 1, "template": {"html": "<p/>", "subject": "s"}}}),
    ("nextmotion_setting", {"kind": "document_template", "op": "duplicate", "item_id": X},
     "duplicate_document_template", (X,), {}),
    ("nextmotion_setting", {"kind": "survey_form", "op": "create", "clinic_id": C,
                            "data": {"type": "bolt_note", "name": "N", "fields_tmpl": {}}},
     "create_survey_form", (C,),
     {"body": {"type": "bolt_note", "name": "N", "fields_tmpl": {}}}),
    ("nextmotion_setting", {"kind": "survey_form", "op": "delete", "item_id": X},
     "delete_survey_form", (X,), {}),
    ("nextmotion_setting", {"kind": "webhook", "op": "update", "item_id": X,
                            "data": {"url": "https://example.invalid/h"}},
     "update_webhook", (X,), {"body": {"url": "https://example.invalid/h"}}),
    ("nextmotion_setting", {"kind": "webhook", "op": "delete", "item_id": X},
     "delete_webhook", (X,), {}),
    ("nextmotion_communication", {"kind": "call", "clinic_id": C,
                                  "data": {"patient": X, "direction": "incoming"}},
     "create_call", (C,), {"body": {"patient": X, "direction": "incoming"}}),
    ("nextmotion_communication", {"kind": "message", "clinic_id": C,
                                  "data": {"communication_template_kind": "email",
                                           "communication_template_type": "invoice",
                                           "object": X}},
     "create_communication_record", (C,),
     {"body": {"communication_template_kind": "email",
               "communication_template_type": "invoice", "object": X}}),
    ("nextmotion_patient", {"op": "create", "clinic_id": C,
                            "data": {"email": "e", "first_name": "f", "last_name": "l",
                                     "gender": "0", "zip_code": "00000"}},
     "create_patient", (C,), {"body": {"email": "e", "first_name": "f", "last_name": "l",
                                       "gender": "0", "zip_code": "00000"}}),
    ("nextmotion_patient", {"op": "update", "patient_id": X,
                            "data": {"email": "e", "first_name": "f", "last_name": "l",
                                     "gender": "1", "has_sms_contact_consent": False}},
     "update_patient", (X,), {"body": {"email": "e", "first_name": "f", "last_name": "l",
                                       "gender": "1", "has_sms_contact_consent": False}}),
]
_IDS = [f"{t}-{k.get('kind', '')}-{k.get('op', '')}" for t, k, *_ in _ECRITURES]


@pytest.mark.parametrize("tool,kwargs,method,args,sent", _ECRITURES, ids=_IDS)
def test_dry_run_false_route_l_ecriture(client, tool, kwargs, method, args, sent):
    _tool(tool)(**kwargs, dry_run=False)
    getattr(client, method).assert_called_once_with(*args, **sent)


def _ecrit(client):
    return [c[0] for c in client.method_calls
            if not c[0].startswith(("get_", "list_"))]


@pytest.mark.parametrize("tool,kwargs,method,args,sent", _ECRITURES, ids=_IDS)
def test_dry_run_par_defaut_n_ecrit_rien(client, tool, kwargs, method, args, sent):
    out = _tool(tool)(**kwargs)
    assert _ecrit(client) == []
    assert out["dry_run"] is True and out["would"] == kwargs.get("op", "create")
    assert "Nothing is written" in out["note"]
    if isinstance(kwargs.get("data"), dict):
        assert kwargs["data"].items() <= out["data"].items()
    elif "data" in kwargs:
        assert out["data"] == kwargs["data"]


def test_dry_run_true_explicite_n_ecrit_pas_non_plus(client):
    _tool("nextmotion_quote")(op="delete", quote_id=X, dry_run=True)
    client.delete_quote.assert_not_called()


def test_l_apercu_relit_l_objet_et_le_projette(client):
    client.get_appointment.return_value = {"data": {
        "id": X, "status": "confirmed", "patient": {"id": X, "last_name": S},
        "calendar_event": {"id": C, "notes": S}}}
    out = _tool("nextmotion_appointment")(op="update", appointment_id=X,
                                          data={"calendar_event": EVENT})
    assert S not in json.dumps(out)
    assert out["appointment"]["patient"] == {"id": X}
    assert out["appointment_id"] == X and "withheld" in out
    client.get_appointment.assert_called_once_with(X)


def test_une_suppression_204_le_dit(client):
    client.delete_webhook.return_value = None
    out = _tool("nextmotion_setting")(kind="webhook", op="delete", item_id=X, dry_run=False)
    assert out == {"deleted": True, "item_id": X}
    client.delete_quote.return_value = None
    out = _tool("nextmotion_quote")(op="delete", quote_id=X, dry_run=False)
    assert out == {"deleted": True, "quote_id": X}


# --- liste blanche d'entrée -------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,match", [
    ("nextmotion_patient", {"op": "create", "clinic_id": C,
                            "data": {"email": "e", "first_name": "f", "last_name": "l",
                                     "gender": "0", "doctor_comments": "x"}},
     "`doctor_comments`"),
    ("nextmotion_patient", {"op": "update", "patient_id": X,
                            "data": {"email": "e", "first_name": "f", "last_name": "l",
                                     "gender": "0", "photograph": "x"}}, "`photograph`"),
    ("nextmotion_appointment", {"op": "update", "appointment_id": X,
                                "data": {"calendar_event": EVENT, "visit": X}}, "`visit`"),
    ("nextmotion_appointment", {"op": "update", "appointment_id": X,
                                "data": {"calendar_event": dict(
                                    EVENT, treatment_session_status="done")}},
     "`data.calendar_event`: .*`treatment_session_status`"),
    ("nextmotion_calendar", {"kind": "opening_hour", "op": "create", "clinic_id": C,
                             "data": {"calendar_event": dict(EVENT, champ_inconnu=1)}},
     "`data.calendar_event`: .*`champ_inconnu`"),
    ("nextmotion_catalog", {"kind": "treatment_type", "op": "update", "item_id": X,
                            "data": {"name": "S", "pricings": [{"price": "1", "x": 1}]}},
     r"`data.pricings\[0\]`: .*`x`"),
    ("nextmotion_catalog", {"kind": "visit_type", "op": "reorder", "clinic_id": C,
                            "data": [{"id": X, "position": 1}]}, r"`data\[0\]`: .*`position`"),
    ("nextmotion_lead", {"op": "create", "clinic_id": C,
                         "data": {"first_name": "f", "last_name": "l", "age": 30}}, "`age`"),
    ("nextmotion_quote", {"op": "update", "quote_id": X,
                          "data": {"treatments": [{"id": X, "price": "1"}]}}, "`treatments`"),
    ("nextmotion_quote", {"op": "validate", "quote_id": X,
                          "data": {"treatments": [{"id": X}]}}, "`treatments`"),
    ("nextmotion_invoice", {"op": "update", "invoice_id": X,
                            "data": {"treatments": [{"id": X}]}}, "`treatments`"),
    ("nextmotion_invoice", {"op": "credit_note", "clinic_id": C,
                            "data": {"patient": X, "items": [{"amount": "1",
                                                              "treatment_id": X}]}},
     r"`data.items\[0\]`: .*`treatment_id`"),
    ("nextmotion_invoice", {"op": "credit_note", "clinic_id": C,
                            "data": {"patient": X, "items": [
                                {"amount": "1", "treatment_package_id": X,
                                 "treatment_package_extract_id": X}]}},
     "`treatment_package_extract_id`, `treatment_package_id`"),
])
def test_un_champ_inconnu_est_refuse_nommement(client, tool, kwargs, match):
    with pytest.raises(McpError, match=match):
        _tool(tool)(**kwargs, dry_run=False)
    assert not client.method_calls


@pytest.mark.parametrize("tool,kwargs,match", [
    ("nextmotion_calendar", {"kind": "room", "op": "create", "clinic_id": C,
                             "data": {"color": "FFFFFFFF"}}, "requires `name`"),
    ("nextmotion_patient", {"op": "create", "clinic_id": C, "data": {"email": "e"}},
     "requires `first_name`, `last_name`, `gender`"),
    ("nextmotion_catalog", {"kind": "visit_type", "op": "update", "item_id": X},
     "requires `data`"),
    ("nextmotion_catalog", {"kind": "visit_type", "op": "reorder", "clinic_id": C,
                            "data": {"id": X}}, "list of objects"),
    ("nextmotion_calendar", {"kind": "room", "op": "update", "item_id": X,
                             "data": [{"name": "x"}]}, "must be an object"),
    ("nextmotion_calendar", {"kind": "room", "op": "create", "item_id": X,
                             "data": {"name": "x"}}, "requires `clinic_id`"),
    ("nextmotion_calendar", {"kind": "room", "op": "delete", "item_id": X,
                             "clinic_id": C}, "does not use `clinic_id`"),
    ("nextmotion_calendar", {"kind": "room", "op": "delete", "item_id": X,
                             "data": {}}, "does not use `data`"),
    ("nextmotion_calendar", {"kind": "room", "op": "delete", "item_id": X,
                             "show_all": False}, "does not use `show_all`"),
    ("nextmotion_calendar", {"kind": "appointment_request", "op": "update", "item_id": X,
                             "data": {}}, "unknown op='update'"),
    ("nextmotion_calendar", {"kind": "room", "clinic_id": C, "dry_run": False},
     "does not use `dry_run`"),
    ("nextmotion_calendar", {"kind": "room", "op": "get", "item_id": X, "data": {}},
     "does not use `data`"),
    ("nextmotion_quote", {"op": "get", "quote_id": X, "dry_run": False},
     "does not use `dry_run`"),
    ("nextmotion_quote", {"op": "update", "data": {}}, "requires `quote_id`"),
    ("nextmotion_invoice", {"op": "pay", "invoice_id": X, "data": {"card": "1"},
                            "offset": 0}, "does not use `offset`"),
    ("nextmotion_patient", {"op": "get", "patient_id": X, "search": "x"},
     "does not use `search`"),
    ("nextmotion_patient", {"op": "create", "clinic_id": C, "patient_id": X,
                            "data": {}}, "does not use `patient_id`"),
    ("nextmotion_catalog", {"kind": "treatment_package_item", "item_id": X, "op": "get"},
     "op='items'"),
    ("nextmotion_catalog", {"kind": "visit_type", "op": "post_treatment", "item_id": X},
     "treatment_type"),
    ("nextmotion_setting", {"kind": "webhook", "op": "placeholders"}, "only applies"),
    ("nextmotion_setting", {"kind": "survey_form", "op": "placeholders"},
     "requires `survey_type`"),
    ("nextmotion_setting", {"kind": "document_template", "op": "placeholders",
                            "survey_type": "bolt_note"}, "does not use `survey_type`"),
    ("nextmotion_communication", {"kind": "message", "clinic_id": C,
                                  "data": {"communication_template_kind": "email",
                                           "communication_template_type": "prescription",
                                           "object": X}}, "is not served"),
])
def test_refus_avant_tout_appel(client, tool, kwargs, match):
    with pytest.raises(McpError, match=match):
        _tool(tool)(**kwargs)
    assert not client.method_calls


# --- l'identité du patient -------------------------------------------------------------

def _fiche():
    return {"id": X, "created_time": "2026-01-01T00:00:00Z", "first_name": "Prénom-Test",
            "last_name": "Nom-Test", "email": "test@example.invalid",
            "phone_number": "+00000", "birth_date": "1900-01-01", "age": "126",
            "gender": "0", "postal_address": "Adresse-Test", "zip_code": "00000",
            "city": "Ville-Test", "country": "FR", "has_sms_contact_consent": "true",
            "patient_number": "P-0001", "is_archived": False,
            "doctor_comments": S, "photograph": {"media_file": S},
            "preview_photograph": {"media_file": S}, "latitude": S, "longitude": S,
            "champ_de_demain": S}


def test_le_patient_sort_avec_son_identite_et_sans_le_clinique(client):
    client.get_patient.return_value = {"data": _fiche()}
    client.list_patients.return_value = {"count": 1, "next": None, "data": [_fiche()]}
    one = _tool("nextmotion_patient")(op="get", patient_id=X)
    page = _tool("nextmotion_patient")(clinic_id=C, search="Nom")
    for fiche in (one["patient"], page["patients"][0]):
        assert S not in json.dumps(fiche)
        assert fiche["last_name"] == "Nom-Test" and fiche["birth_date"] == "1900-01-01"
        assert fiche["postal_address"] == "Adresse-Test" and fiche["country"] == "FR"
        assert fiche["has_sms_contact_consent"] == "true" and fiche["is_archived"] is False
    assert "practitioner comments" in one["withheld"]
    client.list_patients.assert_called_once_with(
        C, search="Nom", birth_date=None, phone_number=None, invoice_total_gt=None,
        is_archived=None, limit=50, offset=0)


def test_la_reponse_d_une_ecriture_patient_repasse_par_la_liste_blanche(client):
    client.create_patient.return_value = {"data": _fiche()}
    out = _tool("nextmotion_patient")(
        op="create", clinic_id=C, dry_run=False,
        data={"email": "e", "first_name": "f", "last_name": "l", "gender": "0"})
    assert S not in json.dumps(out) and out["patient"]["first_name"] == "Prénom-Test"


def test_les_autres_ressources_disent_ou_se_resout_l_id(client):
    for name in ("nextmotion_appointment", "nextmotion_quote", "nextmotion_invoice",
                 "nextmotion_payment", "nextmotion_journey", "nextmotion_patient_stats"):
        desc = asyncio.run(_mcp().get_tool(name)).description
        assert "nextmotion_patient" in desc, name
        assert "no tool resolves" not in desc, name
    from oto_mcp.tools.nextmotion_socle import _WITHHELD
    assert "nextmotion_patient" in _WITHHELD


def test_la_conversion_d_un_lead_rend_le_patient_par_son_id(client):
    client.convert_lead_to_patient.return_value = {"data": _fiche()}
    out = _tool("nextmotion_lead")(op="convert", lead_id=X, dry_run=False)
    assert out["patient"] == {"id": X} and "withheld" in out


# --- secrets, envois, configuration ------------------------------------------------------

def test_les_en_tetes_d_un_webhook_entrent_mais_ne_sortent_jamais(client):
    data = {"action_type": "invoice_created", "url": "https://example.invalid/h",
            "headers": {"Authorization": S}}
    apercu = _tool("nextmotion_setting")(kind="webhook", op="create", clinic_id=C, data=data)
    assert S not in json.dumps(apercu) and apercu["data"]["headers"] == "<masked>"
    client.create_webhook.return_value = {"data": {"id": X, "url": data["url"],
                                                   "headers": {"Authorization": S}}}
    out = _tool("nextmotion_setting")(kind="webhook", op="create", clinic_id=C, data=data,
                                      dry_run=False)
    client.create_webhook.assert_called_once_with(C, body=data)
    assert S not in json.dumps(out) and out["webhook"]["url"] == data["url"]


def test_la_configuration_post_soin_se_lit_sans_piece_jointe(client):
    client.get_post_treatment_config.return_value = {"data": {
        "id": X, "deal_lost_after_seconds": 60,
        "post_follow_up_email": {"delay_seconds": 1, "is_enabled": True,
                                 "pdf_attachment": {"file": S},
                                 "survey_form": {"id": C, "name": "Modèle",
                                                 "fields_tmpl": {"q": S}}}}}
    out = _tool("nextmotion_catalog")(kind="treatment_type", op="post_treatment", item_id=X)
    assert S not in json.dumps(out)
    assert out["post_treatment_config"]["post_follow_up_email"]["survey_form"] == {
        "id": C, "name": "Modèle"}


def test_les_champs_de_fusion_toujours_une_liste(client):
    ph = {"code": "{{nom}}", "label": "Nom", "required": True}
    client.list_survey_form_placeholders.return_value = {
        "data": {"autocomplete_list": [ph], "link_list": []}}
    client.list_document_template_placeholders.return_value = {
        "data": [{"type": 1, "autocomplete_list": [ph], "link_list": [], "x": S}]}
    one = _tool("nextmotion_setting")(kind="survey_form", op="placeholders",
                                      survey_type="bolt_note")
    many = _tool("nextmotion_setting")(kind="document_template", op="placeholders",
                                       document_type=1)
    assert one["placeholders"] == [{"autocomplete_list": [ph], "link_list": []}]
    assert many["placeholders"][0]["type"] == 1 and S not in json.dumps(many)
    client.list_survey_form_placeholders.assert_called_once_with(type="bolt_note")
    client.list_document_template_placeholders.assert_called_once_with(type=1)


def test_un_modele_de_questionnaire_se_liste_par_nom_et_type(client):
    _tool("nextmotion_setting")(kind="survey_form", clinic_id=C, search="n",
                                survey_type="treatment_consent")
    client.list_survey_forms.assert_called_once_with(
        C, search="n", type="treatment_consent", limit=50, offset=0)


def test_aucun_outil_ne_supprime_un_patient_une_facture_ou_un_paiement(client):
    props = {n: asyncio.run(_mcp().get_tool(n)).parameters["properties"]["op"]
             for n in ("nextmotion_patient", "nextmotion_invoice", "nextmotion_payment")}
    assert all("delete" not in json.dumps(p) for p in props.values())


# --- notifications, ids, aperçus -------------------------------------------------------

def test_une_modification_de_rendez_vous_ne_previent_personne_par_defaut(client):
    out = _tool("nextmotion_appointment")(op="update", appointment_id=X,
                                          data={"calendar_event": EVENT})
    assert out["notifie_le_patient"] == []
    assert out["data"]["send_appointment_modified_email"] is False
    assert out["data"]["send_appointment_modified_sms"] is False
    _tool("nextmotion_appointment")(op="update", appointment_id=X, dry_run=False,
                                    data={"calendar_event": EVENT})
    assert client.update_appointment.call_args.kwargs["body"] == {
        "calendar_event": EVENT, "send_appointment_modified_email": False,
        "send_appointment_modified_sms": False}


def test_une_notification_demandee_explicitement_part_et_l_apercu_le_dit(client):
    data = {"calendar_event": EVENT, "send_appointment_modified_sms": True}
    out = _tool("nextmotion_appointment")(op="update", appointment_id=X, data=data)
    assert out["notifie_le_patient"] == ["send_appointment_modified_sms"]
    _tool("nextmotion_appointment")(op="update", appointment_id=X, data=data,
                                    dry_run=False)
    body = client.update_appointment.call_args.kwargs["body"]
    assert body["send_appointment_modified_sms"] is True
    assert body["send_appointment_modified_email"] is False


@pytest.mark.parametrize("tool,kwargs,match", [
    ("nextmotion_patient", {"op": "create", "clinic_id": "pas-un-uuid",
                            "data": {"email": "e", "first_name": "f", "last_name": "l",
                                     "gender": "0"}}, "`clinic_id` must be a UUID"),
    ("nextmotion_catalog", {"kind": "treatment_package_item", "op": "update",
                            "item_id": "../../x", "data": {"pricing": X}},
     "`item_id` must be a UUID"),
    ("nextmotion_communication", {"kind": "message", "clinic_id": "zzz",
                                  "data": {"communication_template_kind": "email",
                                           "communication_template_type": "quote",
                                           "object": X}}, "`clinic_id` must be a UUID"),
    ("nextmotion_quote", {"op": "delete", "quote_id": f"{X}/../x"},
     "`quote_id` must be a UUID"),
    ("nextmotion_invoice", {"op": "credit_note", "clinic_id": C,
                            "data": {"patient": X, "invoice": "zzz",
                                     "items": [{"amount": "1"}]}},
     "`data.invoice` must be a UUID"),
])
def test_l_apercu_refuse_un_id_que_l_ecriture_refuserait(client, tool, kwargs, match):
    with pytest.raises(McpError, match=match):
        _tool(tool)(**kwargs)
    assert not client.method_calls


def test_l_apercu_d_un_avoir_relit_la_facture_qu_il_designe(client):
    client.get_invoice.return_value = {"data": {"id": X, "number_id": "F-1",
                                                "patient": {"id": X, "last_name": S}}}
    data = {"patient": X, "invoice": X, "items": [{"amount": "1"}]}
    out = _tool("nextmotion_invoice")(op="credit_note", clinic_id=C, data=data)
    assert out["invoice"] == {"id": X, "number_id": "F-1", "patient": {"id": X}}
    client.get_invoice.assert_called_once_with(X)
    client.reset_mock()
    out = _tool("nextmotion_invoice")(op="credit_note", clinic_id=C,
                                      data={"patient": X, "items": [{"amount": "1"}]})
    assert "invoice" not in out and not client.method_calls


def test_l_apercu_de_set_items_lit_toutes_les_lignes(client):
    ligne = {"id": C, "sessions": 1}
    client.list_treatment_package_items.side_effect = [
        {"count": 3, "next": "n", "data": [ligne, ligne]},
        {"count": 3, "next": None, "data": [ligne]}]
    out = _tool("nextmotion_catalog")(kind="treatment_package", op="set_items", item_id=X,
                                      data=[{"pricing": C}])
    assert len(out["items"]) == 3 and out["items_complet"] is True
    assert [c.kwargs["offset"] for c in client.list_treatment_package_items.call_args_list
            ] == [0, 100]


def test_un_lead_sert_son_identite_de_contact_sans_ses_notes(client):
    client.get_lead.return_value = {"data": {
        "id": X, "first_name": "Prénom-Test", "last_name": "Nom-Test",
        "email": "lead@example.invalid", "phone_number": "+00000", "notes": S,
        "external_reference": S, "birth_date": S}}
    out = _tool("nextmotion_lead")(op="get", lead_id=X)
    assert out["lead"] == {"id": X, "first_name": "Prénom-Test", "last_name": "Nom-Test",
                           "email": "lead@example.invalid", "phone_number": "+00000"}
    assert "external reference" in out["withheld"]
