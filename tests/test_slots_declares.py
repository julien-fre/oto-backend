"""Cliquet : la forme d'un slot SERVIE est celle que le contrat déclare (#658).

Les slots d'une procédure sont servis depuis ADR 0035 — par `GET
/api/me/guides/{guide_id}`, par la fiche `oto_procedure op=get`, et en écho de chaque
écriture. Leur forme n'était déclarée nulle part : `Optional[list]` nu aux cinq
endroits qui la portent, ce qui donne en OpenAPI un tableau **sans `items`**. Un front
tiers qui dérive son formulaire du contrat ne pouvait donc rien en faire, alors que la
donnée arrivait — d'où l'écran d'équipement resté fermé.

**Le domicile de la forme est `slots.validate_slots`** ; `slots.SlotDecl` en est la
projection déclarée. Deux domiciles pour une même forme, c'est la garantie qu'un champ
ajouté à l'un manquera à l'autre — ce fichier est ce qui l'empêche.

⚠️ Le sens de la comparaison : on part de ce que le VALIDATEUR peut émettre et on
vérifie que le modèle le nomme. L'inverse (un modèle qui déclare un champ jamais servi)
est une promesse creuse, mais c'est le champ servi et non déclaré qu'on répare.
"""
from __future__ import annotations

import pytest

from oto_mcp import slots as slots_mod
from oto_mcp.capabilities.orgs.instructions import (GuideView, InstructionView,
                                                    InstructionWritten)


def _wire_names(model) -> set[str]:
    """Les noms tels qu'ils sortent SUR LE FIL (alias compris) — `declared_schema`
    est servi `schema`, et c'est ce nom-là que le contrat doit porter."""
    return {(f.serialization_alias or f.alias or n)
            for n, f in model.model_fields.items()}


def test_toute_cle_produite_par_le_validateur_est_declaree():
    """Le cœur du cliquet — on fait ÉMETTRE au validateur chacune de ses clés
    facultatives, plutôt que de les lire dans son code."""
    produit = slots_mod.validate_slots([
        {"name": "clients", "type": "tableau", "description": "le tableau des clients",
         "schema": {"fields": [{"key": "email", "type": "text"}]}},
        {"name": "crm", "type": "connecteur", "connector": "folk"},
        {"name": "brief", "type": "doc"},
    ])
    declarees = _wire_names(slots_mod.SlotDecl)
    inconnues = {c for slot in produit for c in slot} - declarees
    assert not inconnues, (
        f"clés produites par `validate_slots` et NON déclarées sur `SlotDecl` : "
        f"{sorted(inconnues)}. Un champ ajouté au validateur se déclare dans le même "
        "lot — sinon il est servi et invisible au contrat (#658).")
    # Le témoin : ce banc doit vraiment avoir fait émettre les clés facultatives.
    emises = {c for slot in produit for c in slot}
    assert {"description", "connector", "schema"} <= emises, \
        f"le banc n'a pas fait émettre les clés facultatives ({sorted(emises)}) — il " \
        "passerait au vert sans avoir rien regardé"


def test_les_types_de_slot_ne_sont_pas_recopies():
    """`SlotType` est DÉRIVÉ de `SLOT_TYPES`. Le recopier ferait diverger les deux
    listes au premier type ajouté — et c'est déjà arrivé une fois dans ce module :
    la docstring d'`InstrSetInput` a annoncé `base` pendant deux mois après son
    renommage en `doc` (2026-07-03)."""
    assert set(slots_mod.SlotType.__args__) == set(slots_mod.SLOT_TYPES)


def test_une_suggestion_porte_son_motif():
    """`suggested_slots` ressemble à `slots` mais porte `reason` en plus — le typer
    `list[SlotDecl]` tairait précisément ce qui fait la suggestion."""
    assert "reason" in _wire_names(slots_mod.SuggestedSlot)
    assert _wire_names(slots_mod.SlotDecl) < _wire_names(slots_mod.SuggestedSlot)


def test_le_check_produit_bien_des_suggestions_de_cette_forme():
    """Éprouvé sur la sortie réelle de `slots_check`, pas sur la lecture de son code."""
    check = slots_mod.slots_check("<slot:absent> et <tool:folk_record>", [])
    declarees = _wire_names(slots_mod.SuggestedSlot)
    for s in check["suggested_slots"]:
        assert not set(s) - declarees, \
            f"clés de suggestion non déclarées : {sorted(set(s) - declarees)}"
    assert check["unresolved_slots"] == ["absent"], \
        "le banc doit voir un slot non résolu, sinon il ne mesure rien"


@pytest.mark.parametrize("modele,champ", [
    (GuideView, "slots"),
    (InstructionView, "slots"),
    (InstructionWritten, "slots"),
    (InstructionWritten, "suggested_slots"),
])
def test_les_sorties_portent_la_forme_et_non_une_liste_nue(modele, champ):
    """Les quatre SORTIES qui servent des slots. Une liste nue rend un tableau sans
    `items` : « une liste de n'importe quoi »."""
    schema = modele.model_json_schema()
    prop = schema["properties"][champ]
    # `Optional[list[X]]` sort en anyOf ; `list[X]` sort à plat. On cherche l'array.
    candidats = prop.get("anyOf") or [prop]
    tableau = next((c for c in candidats if c.get("type") == "array"), None)
    assert tableau is not None, f"{modele.__name__}.{champ} n'est plus un tableau"
    assert tableau.get("items", {}).get("$ref"), (
        f"{modele.__name__}.{champ} rend un tableau SANS `items` typé — c'est "
        "exactement le défaut de #658 : le contrat dit « une liste de n'importe "
        f"quoi » alors que la forme est connue. Reçu : {tableau}")


def test_les_entrees_restent_volontairement_ouvertes():
    """L'asymétrie entrée/sortie est une DÉCISION, pas un oubli : `validate_slots`
    normalise là où un modèle refuserait. Ce test la rend visible — le jour où on
    resserrera l'entrée, il faudra le faire exprès, en supprimant ce test."""
    from oto_mcp.capabilities.orgs.instructions import (AdminInstrSetInput,
                                                        InstrSetInput)
    for modele in (InstrSetInput, AdminInstrSetInput):
        items = modele.model_json_schema()["properties"]["slots"]
        tableau = next((c for c in (items.get("anyOf") or [items])
                        if c.get("type") == "array"), None)
        assert tableau is not None and not tableau.get("items", {}).get("$ref"), (
            f"{modele.__name__}.slots a été resserré. C'est peut-être juste — mais "
            "c'est un changement de ce que le serveur ACCEPTE sur une route déjà "
            "consommée, pas un changement de ce qu'il dit. À faire sciemment.")
    # Et la preuve que le laxisme sert à quelque chose : le validateur normalise une
    # saisie qu'un modèle strict aurait refusée.
    assert slots_mod.validate_slots([{"name": "  CRM  ", "type": "Connecteur"}]) == \
        [{"name": "crm", "type": "connecteur", "connector": "crm"}]
