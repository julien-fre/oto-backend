"""Projection du describe sObject (signal #339).

Le brut d'un Account standard = ~220 Ko / 45 clés, dont 127 childRelationships et
57 clés par champ : le client tronque et déporte en fichier, donc l'agent ne peut
plus chaîner. On vérifie que la projection garde ce qui sert à lire/écrire un champ,
et que `verbose` reste la porte de sortie vers le payload complet.
"""
from oto_mcp.tools.salesforce import _project_describe

RAW = {
    "name": "Account",
    "label": "Compte",
    "labelPlural": "Comptes",
    "custom": False,
    "createable": True,
    "updateable": True,
    "queryable": True,
    "keyPrefix": "001",
    "actionOverrides": [{"formFactor": "LARGE"}] * 20,
    "childRelationships": [{"childSObject": "Case"}] * 127,
    "recordTypeInfos": [{"name": "Master"}],
    "urls": {"sobject": "/services/data/v60.0/sobjects/Account"},
    "fields": [
        {"name": "Id", "label": "ID du compte", "type": "id", "length": 18,
         "nillable": False, "createable": False, "updateable": False,
         "referenceTo": [], "aggregatable": True, "byteLength": 54,
         "compoundFieldName": None, "mask": None, "picklistValues": []},
        {"name": "OwnerId", "label": "Propriétaire", "type": "reference", "length": 18,
         "nillable": False, "createable": True, "updateable": True,
         "referenceTo": ["User"], "byteLength": 54, "picklistValues": []},
        {"name": "Industry", "label": "Secteur", "type": "picklist", "length": 255,
         "nillable": True, "createable": True, "updateable": True, "referenceTo": [],
         "picklistValues": [
             {"active": True, "value": "Technology", "label": "Tech", "validFor": []},
             {"active": True, "value": "Banking", "label": "Banque", "validFor": []},
             {"active": False, "value": "Obsolete", "label": "Obsolète", "validFor": []},
         ]},
    ],
}


def test_object_level_noise_is_dropped():
    out = _project_describe(RAW)
    for noisy in ("childRelationships", "actionOverrides", "recordTypeInfos", "urls"):
        assert noisy not in out
    assert out["name"] == "Account" and out["keyPrefix"] == "001"
    assert out["field_count"] == 3


def test_field_keeps_what_you_need_to_read_or_write_it():
    out = _project_describe(RAW)
    owner = next(f for f in out["fields"] if f["name"] == "OwnerId")
    assert owner["type"] == "reference"
    assert owner["referenceTo"] == ["User"]
    assert owner["createable"] is True and owner["updateable"] is True
    # …et rien des 57 clés de plomberie.
    assert "byteLength" not in owner and "compoundFieldName" not in owner


def test_picklist_is_flattened_to_active_api_values():
    out = _project_describe(RAW)
    industry = next(f for f in out["fields"] if f["name"] == "Industry")
    assert industry["picklistValues"] == ["Technology", "Banking"]  # l'inactive saute


def test_a_field_without_picklist_has_no_picklist_key():
    out = _project_describe(RAW)
    ident = next(f for f in out["fields"] if f["name"] == "Id")
    assert "picklistValues" not in ident
    assert "referenceTo" not in ident      # liste vide = bruit


def test_projection_is_an_order_of_magnitude_lighter():
    import json
    assert len(json.dumps(_project_describe(RAW))) < len(json.dumps(RAW)) / 2


def test_every_field_keeps_its_name_even_when_empty():
    out = _project_describe({"name": "X", "fields": [{"name": "", "type": "string"}]})
    assert out["fields"][0]["name"] == ""
