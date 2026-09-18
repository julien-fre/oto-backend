"""Colonnes calculées par formule OpenFormula (oto-backend#1008).

Sous-ensemble FERMÉ (IFS/IF/SWITCH/AND/OR/NOT/LEFT/MID/LEN/TRUE/FALSE + comparaisons),
analyseur écrit pour cette grammaire précise — jamais `eval()`, jamais une lib de
formules généraliste. Une formule est une fonction PURE de sa propre ligne : pas
d'agrégat cross-lignes, pas de chaînage formule→formule (refusé à la pose).

⚠️ Données 100% fictives (zones A/B/C plutôt que des départements réels, noms
génériques) — aucune donnée métier réelle, aucun nom de client/personne."""
from __future__ import annotations

import pytest

from oto_mcp.datastore import formule as F
from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore.controles import ControlesMixin


# ── tokenizer / parseur / évaluateur ────────────────────────────────────────

BAREME = """IFS(
  AND(OR(LEFT(code;4)="AAAA"; LEFT(code;4)="BBBB"); effectif<>""; effectif<>"inconnu"); "1";
  AND(OR(LEFT(code;4)="AAAA"; LEFT(code;4)="BBBB"); rattache="oui"); "2";
  AND(effectif<>""; effectif<>"inconnu"); "3";
  rattache="oui"; "4";
  TRUE(); "5")"""


def test_bareme_cascade_ordonnee_rang_1():
    noeud = F.parse(BAREME)
    assert F.champs_references(noeud) == {"code", "effectif", "rattache"}
    v, p = F.evaluer_avec_provenance(
        noeud, {"code": "AAAA1", "effectif": "12", "rattache": "non"})
    assert v == "1"
    assert p == ('AND(OR(LEFT(code;4)="AAAA";LEFT(code;4)="BBBB");'
                 'effectif<>"";effectif<>"inconnu")')


def test_bareme_rang_3_effectif_seul():
    v, _ = F.evaluer_avec_provenance(
        F.parse(BAREME), {"code": "ZZZZ", "effectif": "5", "rattache": "non"})
    assert v == "3"


def test_bareme_sentinelle_inconnu_nest_pas_effectif_connu():
    """`effectif="inconnu"` NE DOIT PAS compter comme « effectif connu » — c'est
    exactement le bug qu'un simple `<>\"\"` commettrait."""
    v, _ = F.evaluer_avec_provenance(
        F.parse(BAREME), {"code": "ZZZZ", "effectif": "inconnu", "rattache": "non"})
    assert v == "5"


def test_bareme_defaut_true_rang_5():
    v, p = F.evaluer_avec_provenance(
        F.parse(BAREME), {"code": "ZZZZ", "effectif": "", "rattache": "non"})
    assert v == "5"
    assert p == "TRUE()"


# ── SWITCH, provenance, et le bug préfixe 2 vs 3 chiffres (celui d'Audiens) ──

ZONES = """IFS(
  AND(LEN(code)=5; LEFT(code;2)="AA"); SWITCH(code; "AA001";"Alice"; "AA002";"Bob"; "");
  LEN(code)=5; SWITCH(IF(OR(LEFT(code;3)="BB1"; LEFT(code;3)="BB2"); LEFT(code;3); LEFT(code;2)); "BB1";"Carla"; "CC";"Dan"; "");
  TRUE(); "")"""


def test_switch_zone_courte_deux_chiffres():
    v, p = F.evaluer_avec_provenance(F.parse(ZONES), {"code": "CC123"})
    assert v == "Dan"
    assert "'CC'" in p and "'Dan'" in p


def test_switch_zone_prefixe_trois_chiffres_seulement_les_bons():
    """Le patron du bug réel (Monaco/98) : un préfixe à 3 chiffres ne doit
    s'appliquer QU'AUX codes qui le déclarent explicitement — pas à tout ce qui
    partage les 2 premiers chiffres."""
    v, _ = F.evaluer_avec_provenance(F.parse(ZONES), {"code": "BB1XX"})
    assert v == "Carla"
    # "BB9XX" partage le préfixe 2 chiffres "BB" avec "BB1"/"BB2" mais n'a pas de
    # correspondance 3-chiffres déclarée : il doit retomber sur le SWITCH 2-chiffres,
    # ce qui ne matche rien ("BB" seul n'est pas une clé) -> vide.
    v2, _ = F.evaluer_avec_provenance(F.parse(ZONES), {"code": "BB9XX"})
    assert v2 == ""


def test_hors_correspondance_reste_vide_pas_invente():
    v, _ = F.evaluer_avec_provenance(F.parse(ZONES), {"code": "ZZ999"})
    assert v == ""


def test_code_incomplet_tombe_sur_le_defaut():
    v, _ = F.evaluer_avec_provenance(F.parse(ZONES), {"code": "AB1"})
    assert v == ""


# ── refus à l'analyse ────────────────────────────────────────────────────────

def test_fonction_hors_sous_ensemble_refusee_et_nommee():
    with pytest.raises(F.FormulaError, match="VLOOKUP"):
        F.parse("VLOOKUP(a;b;1)")


def test_caractere_inattendu_refuse():
    with pytest.raises(F.FormulaError):
        F.parse("a & b")


def test_arite_ifs_impaire_refusee_a_l_evaluation():
    with pytest.raises(F.FormulaError, match="IFS"):
        F.evaluer_avec_provenance(F.parse('IFS(TRUE();"1";TRUE())'), {})


def test_if_arite_fixe_a_trois():
    with pytest.raises(F.FormulaError, match="3 argument"):
        F.parse('IF(TRUE();"1")')


# ── validation contre un schéma (colonne inconnue, chaînage) ─────────────────

def test_valider_colonne_inconnue_refusee_et_nommee():
    with pytest.raises(F.FormulaError, match="fantome"):
        F.valider('IFS(fantome="x";"1";TRUE();"2")', {"autre"}, set())


def test_valider_chainage_refuse():
    with pytest.raises(F.FormulaError, match="chaînage"):
        F.valider('IFS(autre_calc="x";"1";TRUE();"2")',
                  {"autre_calc"}, {"autre_calc"})


def test_valider_formule_saine_rend_last_node():
    noeud = F.valider('IFS(code="x";"1";TRUE();"2")', {"code"}, set())
    assert isinstance(noeud, F.Appel) and noeud.fonction == "IFS"


# ── re-sérialisation canonique (déterministe) ─────────────────────────────────

def test_serialiser_est_pur_et_deterministe():
    noeud = F.parse(BAREME)
    a = F.serialiser(noeud)
    b = F.serialiser(F.parse(BAREME))
    assert a == b


# ── déclaration de schéma : refus à la pose, readonly implicite ──────────────

def _schema_zones():
    return {"fields": [
        {"key": "code", "type": "text"},
        {"key": "zone", "type": "formula", "formula": ZONES},
    ]}


def test_schema_avec_formule_saine_valide():
    assert dsv2.validate_schema_def(_schema_zones()) == []


def test_schema_formule_refusee_a_la_pose_fonction_inconnue():
    bad = {"fields": [
        {"key": "code", "type": "text"},
        {"key": "zone", "type": "formula", "formula": "VLOOKUP(code;1;2)"},
    ]}
    errs = dsv2.validate_schema_def(bad)
    assert errs and "VLOOKUP" in errs[0]


def test_schema_formule_refusee_a_la_pose_colonne_inconnue():
    bad = {"fields": [
        {"key": "code", "type": "text"},
        {"key": "zone", "type": "formula", "formula": 'IFS(fantome="x";"1";TRUE();"2")'},
    ]}
    errs = dsv2.validate_schema_def(bad)
    assert errs and "fantome" in errs[0]


def test_schema_formule_refusee_a_la_pose_chainage():
    bad = {"fields": [
        {"key": "code", "type": "text"},
        {"key": "a", "type": "formula", "formula": 'IFS(code="x";"1";TRUE();"2")'},
        {"key": "b", "type": "formula", "formula": 'IFS(a="1";"oui";TRUE();"non")'},
    ]}
    errs = dsv2.validate_schema_def(bad)
    assert errs and any("chaînage" in e for e in errs)


def test_schema_formule_sans_texte_refusee():
    bad = {"fields": [{"key": "zone", "type": "formula"}]}
    errs = dsv2.validate_schema_def(bad)
    assert errs and "formula" in errs[0]


def test_colonne_formule_est_readonly_implicite():
    assert dsv2.readonly_fields(_schema_zones()) == {"zone"}


def test_colonne_formule_apparait_dans_les_cles_reconnues():
    from oto_mcp.datastore import schema_keys
    assert "formula" in schema_keys.RECONNUES


# ── recalcul à l'écriture (seam _check_row, via le mixin réel) ───────────────

class _Store(ControlesMixin):
    off_geles: dict = {}
    off_rejected: list = []


def test_appliquer_formules_calcule_et_pose_le_comment():
    store = _Store()
    merged = {"code": "AA001"}
    store._appliquer_formules(_schema_zones(), merged)
    assert merged["zone"]["valeur"] == "Alice"
    assert "comment" in merged["zone"]


def test_appliquer_formules_ne_touche_jamais_la_couche_origine():
    store = _Store()
    merged = {"code": "AA001",
             "zone": {"origine": {"valeur": "VALEUR HISTORIQUE DE LA CLIENTE"}}}
    store._appliquer_formules(_schema_zones(), merged)
    assert merged["zone"]["origine"] == {"valeur": "VALEUR HISTORIQUE DE LA CLIENTE"}
    assert merged["zone"]["valeur"] == "Alice"


def test_appliquer_formules_noop_sans_colonne_formule():
    store = _Store()
    schema_sans_formule = {"fields": [{"key": "code", "type": "text"}]}
    merged = {"code": "AA001"}
    store._appliquer_formules(schema_sans_formule, merged)
    assert merged == {"code": "AA001"}


def test_appliquer_formules_recalcule_quand_une_entree_change():
    """La propriété demandée : changer une colonne d'entrée fait recalculer la
    formule qui en dépend, sur la MÊME écriture."""
    store = _Store()
    merged = {"code": "AA001"}
    store._appliquer_formules(_schema_zones(), merged)
    assert merged["zone"]["valeur"] == "Alice"
    merged["code"] = "AA002"
    store._appliquer_formules(_schema_zones(), merged)
    assert merged["zone"]["valeur"] == "Bob"
