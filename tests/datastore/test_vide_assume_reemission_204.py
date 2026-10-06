"""Le vide ASSUMÉ se relit sous la forme qui le RÉÉCRIT (oto#204, étape 2).

**Le défaut.** Une liste sans `of.key` se remplace en bloc. Relue au défaut puis réémise, la
case au vide assumé revenait `""` — un vide ORDINAIRE — et l'écriture était refusée. Le client
ne pouvait pas savoir que ce `""`-là était assumé : les deux vides se servaient pareil, donc
il inventait une valeur, ou posait `@empty` sur un vide que personne n'avait cherché.

**La réponse** (option 2 du plan) : `empties=sentinel` sert ces cases `"@empty"`, le mot qui les
écrit ; réémis tel quel, il repose le marqueur. Le défaut (`plain`), la page publique et
l'export ne changent pas.

Ce banc tient ce qui se juge SANS base : la forme servie, le refus nommé, la parité des
surfaces, et ce que le mot réservé `@empty` fait d'une case (`@keep` et `@clear`, retirés,
sont refusés avant la fusion). Les
chemins d'écriture, l'aller-retour et la concurrence sont au banc `_live`.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path

import pytest

from oto_mcp.datastore import columns as dcol
from oto_mcp.datastore import couches as dsc
from oto_mcp.datastore import layers as dsl
from oto_mcp.datastore import reliques as rq
from oto_mcp.datastore.columns import (
    _merge_column,
    effacements_report,
    mots_resolus_a_la_creation,
    refuser_les_mots_mal_places,
    vides_assumes_perdus,
)
from oto_mcp.datastore.core import DatastorePg
from oto_mcp.datastore.errors import RowValidationError
from oto_mcp.datastore.validation import validate_row

RACINE = Path(__file__).resolve().parents[2]
MARQUEE = {"valeur": "", dsc.VIDE_ASSUME: True}
SCHEMA = {"unknown_columns": "report", "fields": [
    {"key": "raison", "type": "text"},
    {"key": "fonction", "type": "text", "required": True},
    {"key": "contacts", "type": "list", "of": {"type": "object", "fields": [
        {"key": "nom", "type": "text", "required": True},
        {"key": "fonction", "type": "text", "required": True},
        {"key": "note", "type": "text"}]}},
]}
DONNEES = {
    "raison": "ACME",
    "fonction": {**MARQUEE, "comment": "aucune source ne donne ce titre"},
    "contacts": [{"nom": "Alice", "fonction": MARQUEE},
                 {"nom": "Bruno", "fonction": "DAF", "note": ""},
                 {"nom": "Chloé", "fonction": {**MARQUEE, "comment": "registre muet"}}],
}


def _servie(data: dict = DONNEES, **forme) -> dict:
    ligne = {"row_id": "r1", "created_at": "t", "updated_at": "t", "data": data}
    return DatastorePg._row_to_dict(ligne, SCHEMA, **forme)


def _aucune_fuite(obj) -> None:
    assert "vide_assume" not in json.dumps(obj, ensure_ascii=False, default=str), (
        "le marqueur interne FUIT dans ce qui est servi")


# ── la forme servie ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("layers", [dsl.FLAT, dsl.NESTED])
def test_le_defaut_ne_change_rien(layers):
    """`plain` est la forme d'avant ce paramètre : nommée ou omise, la même ligne."""
    sans = _servie(layers=layers)
    assert _servie(layers=layers, empties=dsl.PLAIN) == sans
    assert dsc.VIDE_DELIBERE not in json.dumps(sans, ensure_ascii=False)
    _aucune_fuite(sans)


def test_sentinel_a_plat_sert_le_mot_au_premier_niveau_et_dans_la_liste():
    servie = _servie(empties=dsl.SENTINEL)
    assert servie["fonction"] == dsc.VIDE_DELIBERE
    assert servie["fonction.comment"] == "aucune source ne donne ce titre", (
        "la couche reste à côté du mot, comme à côté de la valeur")
    alice, bruno, chloe = servie["contacts"]
    assert alice == {"nom": "Alice", "fonction": dsc.VIDE_DELIBERE}
    assert bruno == {"nom": "Bruno", "fonction": "DAF", "note": ""}, (
        "un vide ORDINAIRE reste `\"\"` : seul le marqueur change la forme")
    assert chloe == {"nom": "Chloé", "fonction": dsc.VIDE_DELIBERE,
                     "fonction.comment": "registre muet"}
    _aucune_fuite(servie)


def test_sentinel_imbrique_enveloppe_le_mot_meme_sans_autre_couche():
    servie = _servie(layers=dsl.NESTED, empties=dsl.SENTINEL)
    assert servie["fonction"] == {"valeur": dsc.VIDE_DELIBERE,
                                  "comment": "aucune source ne donne ce titre"}
    alice, bruno, chloe = servie["contacts"]
    assert alice["fonction"] == {"valeur": dsc.VIDE_DELIBERE}
    assert bruno["fonction"] == "DAF" and bruno["note"] == ""
    assert chloe["fonction"] == {"valeur": dsc.VIDE_DELIBERE, "comment": "registre muet"}
    _aucune_fuite(servie)


def test_un_vide_ordinaire_au_premier_niveau_reste_vide_en_sentinel():
    servie = _servie({"fonction": "", "raison": {"valeur": "", "comment": "x"}},
                     empties=dsl.SENTINEL)
    assert servie["fonction"] == "" and servie["raison"] == ""


# ── le refus nommé ───────────────────────────────────────────────────────────

def test_check_empties_nomme_le_parametre_et_les_valeurs_admises():
    assert dsl.check_empties(None) == dsl.PLAIN
    assert dsl.check_empties("sentinel") == dsl.SENTINEL
    with pytest.raises(ValueError) as e:
        dsl.check_empties("@empty")
    message = str(e.value)
    assert "`empties`" in message and "`plain`" in message and "`sentinel`" in message


def test_la_face_REST_refuse_en_400_invalid_empties():
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.capabilities.datastore._forme import _empties, _relais_empties
    with pytest.raises(AuthzDenied) as e:
        _empties("oui")
    assert (e.value.status, e.value.code) == (400, "invalid_empties")
    assert "`sentinel`" in str(e.value)
    assert _relais_empties(None) == {} and _relais_empties("plain") == {}
    assert _relais_empties("sentinel") == {"empties": "sentinel"}


# ── la parité : toute lecture qui accepte `layers` accepte `empties` ─────────

def _accepteurs_de_layers() -> dict:
    """`{nom qualifié: porte empties ?}` pour chaque fonction ou modèle du paquet qui
    accepte une FORME de lecture `layers` (annotée `str`) — lu dans le source, pour qu'une
    lecture neuve n'ait pas besoin d'être déclarée ici pour être tenue."""
    out: dict = {}
    for chemin in sorted((RACINE / "oto_mcp").rglob("*.py")):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        relatif = chemin.relative_to(RACINE).as_posix()
        for noeud in ast.walk(arbre):
            if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [*noeud.args.posonlyargs, *noeud.args.args, *noeud.args.kwonlyargs]
                noms = {a.arg: a for a in args}
                layers = noms.get("layers")
                if layers is not None and ast.unparse(layers.annotation or ast.Pass()) == "str":
                    out[f"{relatif}::{noeud.name}"] = "empties" in noms
            elif isinstance(noeud, ast.ClassDef):
                champs = {c.target.id: c for c in noeud.body
                          if isinstance(c, ast.AnnAssign) and isinstance(c.target, ast.Name)}
                layers = champs.get("layers")
                if layers is not None and ast.unparse(layers.annotation) == "str":
                    out[f"{relatif}::{noeud.name}"] = "empties" in champs
    return out


def test_toute_lecture_qui_accepte_layers_accepte_empties_dans_le_source():
    accepteurs = _accepteurs_de_layers()
    attendus = {"_row_to_dict", "get_row", "cursor_rows", "page_rows", "claim_next",
                "claim_row", "data_rows", "data_claim_next", "ListRowsInput",
                "GetRowInput", "ClaimNextInput", "ClaimRowInput"}
    vus = {nom.rpartition("::")[2] for nom in accepteurs}
    assert attendus <= vus, f"le balayage est devenu aveugle : manquent {attendus - vus}"
    sans = sorted(nom for nom, porte in accepteurs.items() if not porte)
    assert not sans, f"lectures qui acceptent `layers` sans `empties` : {sans}"


def test_toute_operation_REST_qui_sert_layers_sert_empties():
    from oto_mcp import openapi
    doc = openapi.build()
    vues = 0
    for chemin, operations in doc["paths"].items():
        for verbe, op in operations.items():
            noms = {p.get("name") for p in op.get("parameters", [])}
            corps = ((op.get("requestBody") or {}).get("content") or {}).get(
                "application/json", {}).get("schema", {})
            noms |= set(corps.get("properties") or {})
            if "layers" in noms:
                vues += 1
                assert "empties" in noms, f"{verbe.upper()} {chemin} sert `layers` sans `empties`"
    assert vues >= 4, "rows, rows/{id}, claim_next et claim doivent être vus"


def test_tout_outil_MCP_qui_sert_layers_sert_empties():
    from fastmcp import FastMCP

    from oto_mcp.tools import datastore as tools_ds
    mcp = FastMCP("parite-204")
    tools_ds.register(mcp)
    outils = asyncio.run(mcp.list_tools())
    vus = []
    for outil in outils:
        props = (outil.parameters or {}).get("properties") or {}
        if "layers" in props:
            vus.append(outil.name)
            assert "empties" in props, f"{outil.name} sert `layers` sans `empties`"
            assert props["empties"].get("default") == dsl.PLAIN
    assert {"data_rows", "data_claim_next"} <= set(vus)


# ── les mots réservés : une table, et ce que chacun fait d'une case ──────────

MARQUEUR = dsc.VIDE_ASSUME
REMPLIE = {"valeur": "Directrice", "comment": "site", "link": "https://exemple.test/a",
           "origine": "DG"}
SANS_CLE = SCHEMA["fields"][2]
AVEC_CLE = {"key": "contacts", "type": "list", "of": {"type": "object", "key": "role",
            "fields": [{"key": "role", "type": "text"},
                       {"key": "fonction", "type": "text", "required": True},
                       {"key": "note", "type": "text"}]}}


def test_seul_empty_est_un_mot_que_la_fusion_resout():
    """`@keep` et `@clear` sont refusés à l'entrée : la fusion ne les connaît plus, et
    ne doit pas les reconnaître (sinon un chemin non gardé les « résoudrait »)."""
    assert dsc.est_vide_delibere(dsc.VIDE_DELIBERE)
    assert not dsc.est_vide_delibere(dsc.GARDE) and not dsc.est_vide_delibere(dsc.EFFACEMENT)
    assert not hasattr(dcol, "_MOTS")


def test_empty_vide_une_valeur_en_place_et_assume_le_vide():
    assert _merge_column(REMPLIE, dsc.VIDE_DELIBERE) == {"valeur": "", "origine": "DG",
                                                        MARQUEUR: True}
    assert _merge_column(None, dsc.VIDE_DELIBERE) == MARQUEE






def test_4a_empty_en_couches_sur_une_case_deja_vide_garde_ce_qui_n_est_pas_envoye():
    vide = {"valeur": "", "link": "https://exemple.test/b"}
    assert _merge_column(vide, {"valeur": dsc.VIDE_DELIBERE, "comment": "rien"}) == {
        "valeur": "", "link": "https://exemple.test/b", "comment": "rien", MARQUEUR: True}


def test_4a_empty_en_couches_sur_une_valeur_en_place_fait_tomber_comment_et_link():
    assert _merge_column(REMPLIE, {"valeur": dsc.VIDE_DELIBERE, "comment": "rien"}) == {
        "valeur": "", "origine": "DG", "comment": "rien", MARQUEUR: True}


@pytest.mark.parametrize("mot", [dsc.VIDE_DELIBERE])
def test_4b_un_mot_dans_une_couche_ne_touche_que_la_couche(mot):
    annotee = {**MARQUEE, "comment": "aucune source", "link": "https://exemple.test/c"}
    assert _merge_column(annotee, {"comment": mot}) == {**annotee, "comment": ""}, (
        "ni la valeur ni le marqueur ne bougent")
    assert _merge_column(REMPLIE, {"link": mot}) == {**REMPLIE, "link": ""}
    assert not dsc.vide_assume(_merge_column(REMPLIE, {"link": mot}))


def test_4c_une_origine_renvoyee_inchangee_ne_change_rien_au_geste():
    assert _merge_column(REMPLIE, {"valeur": dsc.VIDE_DELIBERE, "origine": "DG"}) == {
        "valeur": "", "origine": "DG", MARQUEUR: True}




def test_liste_sans_cle_empty_marque():
    out = _merge_column([{"nom": "A", "fonction": "DG"}], [
        {"nom": "A", "fonction": dsc.VIDE_DELIBERE},
        {"nom": "B", "fonction": "",
         "note": {"valeur": dsc.VIDE_DELIBERE, "comment": "rien"}},
        {"nom": "A", "fonction": dsc.VIDE_DELIBERE}], SANS_CLE)
    assert out == [{"nom": "A", "fonction": MARQUEE},
                   {"nom": "B", "fonction": "",
                    "note": {"valeur": "", "comment": "rien", MARQUEUR: True}},
                   {"nom": "A", "fonction": MARQUEE}], "deux éléments identiques, deux marqueurs"


def test_liste_a_cle_element_nouveau_resolu():
    avant = [{"role": "rh", "fonction": "DRH"}, {"role": "paie", "fonction": MARQUEE}]
    out = _merge_column(avant, [{"role": "paie", "fonction": ""},
                                {"role": "rh", "fonction": None},
                                {"role": "achats", "fonction": dsc.VIDE_DELIBERE}], AVEC_CLE)
    assert out == [{"role": "paie", "fonction": MARQUEE},
                   {"role": "rh"},
                   {"role": "achats", "fonction": MARQUEE}]


SCHEMA_MOTS = {"fields": [
    {"key": "tags", "type": "list", "of": {"type": "text"}},
    {"key": "adresse", "type": "object", "fields": [{"key": "rue", "type": "text"}]},
    {"key": "brut", "type": "json"},
    AVEC_CLE,
]}


@pytest.mark.parametrize("corps, chemin", [
    ({"tags": ["a", dsc.VIDE_DELIBERE]}, "tags[1]"),
    ({"adresse": {"rue": dsc.VIDE_DELIBERE}}, "adresse.rue"),
    ({"brut": {"a": [dsc.VIDE_DELIBERE]}}, "brut.a[0]"),
    ({"brut": [{"a": dsc.VIDE_DELIBERE}]}, "brut[0].a"),
    ({"contacts": [{"role": dsc.VIDE_DELIBERE, "fonction": "x"}]}, "contacts[0].role"),
    ({"contacts": [{"role": {"valeur": dsc.VIDE_DELIBERE}, "fonction": "x"}]},
     "contacts[0].role"),
    ({"contacts": [{"role": "rh", "fonction": {"valeur": [dsc.VIDE_DELIBERE]}}]},
     "contacts[0].fonction[0]"),
], ids=["liste_de_valeurs", "sous_champ_objet", "json_objet", "json_liste",
        "identite_nue", "identite_en_couches", "contenu_d_attribut"])
def test_un_mot_hors_d_une_case_est_refuse_en_nommant_le_chemin(corps, chemin):
    with pytest.raises(RowValidationError) as e:
        refuser_les_mots_mal_places(SCHEMA_MOTS, corps)
    assert f"`{chemin}`" in str(e.value)


def test_les_cases_et_leurs_couches_admettent_les_mots():
    refuser_les_mots_mal_places(SCHEMA_MOTS, {
        "brut": dsc.VIDE_DELIBERE, "tags": dsc.VIDE_DELIBERE,
        "adresse": {"valeur": dsc.VIDE_DELIBERE, "comment": dsc.VIDE_DELIBERE},
        "contacts": [{"role": {"valeur": "rh", "comment": dsc.VIDE_DELIBERE},
                      "fonction": dsc.VIDE_DELIBERE,
                      "note": {"valeur": "x", "comment": dsc.VIDE_DELIBERE}}]})


def test_a_la_creation_les_mots_se_resolvent_sur_une_copie():
    corps = {"raison": "ACME", "fonction": dsc.VIDE_DELIBERE,
             "contacts": [{"nom": "A", "fonction": dsc.VIDE_DELIBERE}]}
    out = mots_resolus_a_la_creation(SCHEMA, corps)
    assert out == {"raison": "ACME", "fonction": MARQUEE,
                   "contacts": [{"nom": "A", "fonction": MARQUEE}]}
    assert corps["fonction"] == dsc.VIDE_DELIBERE, "le geste reçu n'est pas modifié"
    sans_mot = {"raison": "ACME"}
    assert mots_resolus_a_la_creation(SCHEMA, sans_mot) == sans_mot


AVANT = [{"nom": "A", "note": MARQUEE}, {"nom": "B", "note": "x"}]


@pytest.mark.parametrize("apres, attendu", [
    ([{"nom": "A", "note": ""}, {"nom": "B", "note": "x"}], ["contacts[0].note"]),
    ([{"nom": "A"}, {"nom": "B", "note": "x"}], ["contacts[0].note"]),
    ([{"nom": "B", "note": "x"}, {"nom": "A", "note": ""}], ["contacts[1].note"]),
    ([{"nom": "A", "note": MARQUEE}, {"nom": "B", "note": "x"}], []),
    ([{"nom": "A", "note": "trouvée"}, {"nom": "B", "note": "x"}], []),
    ([{"nom": "B", "note": "x"}], []),
], ids=["relue_au_defaut", "attribut_retire", "reordonnee_au_defaut", "renvoyee_en_sentinel",
        "vraie_valeur", "element_retire"])
def test_un_vide_assume_rendu_ordinaire_se_releve_et_rien_d_autre(apres, attendu):
    out = vides_assumes_perdus(AVANT, apres, "contacts", "r1")
    assert [r["champ"] for r in out] == attendu
    assert all(r["couche"] == dsc.VIDE_DELIBERE for r in out)


def test_le_releve_dit_la_relecture_et_ne_fuit_pas():
    perdus = vides_assumes_perdus(AVANT, [{"nom": "A", "note": ""}, {"nom": "B", "note": "x"}],
                                  "contacts", "r1")
    rep = effacements_report(perdus + [{"ligne": "r1", "champ": "contacts", "valeur": AVANT}])
    assert "empties=sentinel" in rep["couches_effacees_hint"]
    # oto#140 (23/09/2026) : `@clear` est déprécié — le relevé nomme `null` et `@empty`.
    assert "`null`" in rep["valeurs_effacees_hint"]
    assert "`@empty`" in rep["valeurs_effacees_hint"]
    assert "@clear" not in rep["valeurs_effacees_hint"]
    _aucune_fuite(rep)


def test_le_refus_de_requis_nomme_le_chemin_le_mot_et_la_relecture():
    erreurs = validate_row(SCHEMA, {"fonction": "DG", "contacts": [{"nom": "A", "fonction": ""}]})
    message = " ".join(erreurs)
    assert "contacts[0].fonction" in message and "requis" in message
    assert "`@empty`" in message and "empties=sentinel" in message


@pytest.mark.parametrize("mot", [dsc.VIDE_DELIBERE, None])
def test_empty_sur_une_relique_est_un_effacement_comme_null(mot):
    avant = {"c": {"valeur": "x"}, "c.link": "https://exemple.test/relique"}
    assert rq.effacements_sur_relique({"c": {"link": mot}}, avant) == ["c.link"]
    assert rq.effacements_sur_relique({"c.link": mot}, avant) == ["c.link"]


def test_l_exemple_servi_par_data_write_est_celui_que_le_store_resout():
    from oto_mcp.tools import datastore as tools_ds
    exemple = '{"contacts": [{"nom": "Alice", "fonction": "@empty"}]}'
    assert exemple in inspect.getsource(tools_ds)
    out = _merge_column(None, json.loads(exemple)["contacts"], SANS_CLE)
    assert out == [{"nom": "Alice", "fonction": MARQUEE}]
