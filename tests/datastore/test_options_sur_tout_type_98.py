"""#98 — une liste de valeurs DÉCLARÉE engage sur tout type scalaire, pas seulement `enum`.

Mesuré au sommet le 10/09/2026, avant ce correctif (valeur 'zzz', options [a, b]) :

    régime  type    signalé  refusé
    souple  text    non      non
    souple  json    non      non
    souple  —       non      non
    souple  enum    oui      non
    strict  text    non      non     ← un tableau STRICT acceptait tout, sans un mot
    strict  json    non      non
    strict  —       non      non
    strict  enum    —        oui

Les deux premiers bancs rejouent exactement cette sonde : ils rougissent si le trou se
rouvre, sur n'importe quelle case.

Empreinte en production le même jour, avant d'armer (lecture seule) : UN tableau armé
porte des options sur une colonne non-enum (22 lignes, 1 valeur hors liste), aucun
tableau souple, un sous-champ. La mesure a été éprouvée sur une population témoin :
elle retrouve les 151 tableaux souples à options `enum` comptés la veille. Et aucune
ligne ne peut geler : une colonne que le geste n'écrit pas se SIGNALE (dernier banc).
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import non_applique as na
from oto_mcp.datastore import validation as V
from oto_mcp.datastore.options_declarees import hors_des_options, valeur_comparee

# Des valeurs qui PASSENT la forme de leur type : on ne juge ici que l'appartenance.
HORS = {"text": "zzz", "json": "zzz", None: "zzz",
        "url": "https://zzz.example", "email": "z@zzz.example"}


def _schema(ftype, *, strict, options=("a", "b"), autres=()):
    f = {"key": "x", "options": list(options)}
    if ftype:
        f["type"] = ftype
    return {**({"unknown_columns": "report"} if strict else {}), "fields": [f, *autres]}


@pytest.mark.parametrize("ftype", list(HORS))
def test_STRICT_refuse_une_valeur_hors_liste_sur_tout_type_scalaire(ftype):
    hors: list = []
    errs = V.validate_row(_schema(ftype, strict=True), {"x": HORS[ftype]}, hors=hors)
    assert len(errs) == 1 and "hors options" in errs[0], errs
    # Un refus, un relevé : c'est la condition exacte de l'écartement (#667). Deux refus
    # pour un relevé feraient refuser la fiche entière au lieu d'écarter la valeur.
    assert [h["champ"] for h in hors] == ["x"]


@pytest.mark.parametrize("ftype", list(HORS))
def test_SOUPLE_signale_une_valeur_hors_liste_sur_tout_type_scalaire(ftype):
    schema = _schema(ftype, strict=False)
    assert na.unenforced_options(schema, {"x": HORS[ftype]}) == {"x": HORS[ftype]}
    assert V.validate_row(schema, {"x": HORS[ftype]}) == [], "le régime souple ne refuse pas"


def test_une_valeur_de_la_liste_passe_dans_les_deux_regimes():
    assert V.validate_row(_schema("text", strict=True), {"x": "a"}) == []
    assert na.unenforced_options(_schema("text", strict=False), {"x": "a"}) == {}


def test_une_valeur_mal_formee_n_est_jamais_jugee_sur_la_liste():
    """La forme d'abord. Un nombre mal formé porte déjà ses refus (le type, et la
    consigne de `types_trahis`) : la liste ne doit rien y AJOUTER — surtout pas une
    entrée `hors`, qui ferait croire à l'écriture qu'elle peut écarter la valeur et
    écrire le reste, alors que la valeur n'a même pas la forme déclarée."""
    sans_liste = V.validate_row({"unknown_columns": "report", "fields": [{"key": "x", "type": "number"}]},
                                {"x": "abc"})
    hors: list = []
    avec_liste = V.validate_row(_schema("number", strict=True, options=("1", "2")),
                                {"x": "abc"}, hors=hors)
    assert avec_liste == sans_liste and sans_liste, (sans_liste, avec_liste)
    assert hors == [] and not any("hors options" in e for e in avec_liste)


def test_un_nombre_se_compare_comme_la_base_le_rend():
    """`data->>champ` rend `3` pour 3 et `3.0` pour 3.0 — le jugement Python aussi,
    sinon le relevé de la pose et le refus de l'écriture se contrediraient."""
    schema = _schema("number", strict=True, options=("3",))
    assert V.validate_row(schema, {"x": 3}) == []
    assert V.validate_row(schema, {"x": "3"}) == []
    assert len(V.validate_row(schema, {"x": 3.5})) == 1
    assert (valeur_comparee(3), valeur_comparee(3.0), valeur_comparee(True)) == ("3", "3.0", "true")


def test_un_composite_n_est_jamais_une_option_et_se_cite_en_JSON():
    hors: list = []
    errs = V.validate_row(_schema("json", strict=True), {"x": {"a": 1}}, hors=hors)
    assert len(errs) == 1 and len(hors) == 1
    assert '{"a": 1}' in errs[0] and "{'a'" not in errs[0], "jamais le repr d'un dict Python"


def test_le_vide_n_est_pas_hors_liste():
    errs = V.validate_row(_schema("text", strict=True), {"x": ""})
    assert not any("hors options" in e for e in errs), errs
    assert na.unenforced_options(_schema("text", strict=False), {"x": ""}) == {}
    assert hors_des_options("zzz", []) is False and hors_des_options("zzz", None) is False


def test_une_valeur_en_couches_est_deballee_avant_d_etre_jugee():
    schema = _schema("text", strict=True)
    assert V.validate_row(schema, {"x": {"valeur": "a", "comment": "vu"}}) == []
    assert len(V.validate_row(schema, {"x": {"valeur": "zzz", "comment": "vu"}})) == 1


def test_les_elements_d_une_colonne_liste_suivent_la_meme_regle():
    hors: list = []
    schema = {"unknown_columns": "report", "fields": [
        {"key": "tags", "type": "list", "of": {"type": "text", "options": ["a", "b"]}}]}
    errs = V.validate_row(schema, {"tags": ["a", "zzz"]}, hors=hors)
    assert len(errs) == 1 and "tags[1]" in errs[0], errs
    assert [h["champ"] for h in hors] == ["tags[1]"]


def test_une_colonne_que_le_geste_n_ecrit_pas_se_signale_sans_geler():
    """Le rayon d'impact du correctif tient à ce banc : une valeur hors liste DÉJÀ en
    base ne rend pas la ligne inécritable pour un geste qui écrit une autre colonne."""
    schema = _schema("text", strict=True, autres=({"key": "y", "type": "text"},))
    gelees: list = []
    errs = V.validate_row(schema, {"x": "zzz", "y": "ok"}, written={"y"}, gelees=gelees)
    assert errs == [], errs
    assert [g["champ"] for g in gelees] == ["x"]


def test_la_pose_annonce_les_listes_non_appliquees_sur_tout_type():
    assert na.options_not_enforced(_schema("text", strict=False)) == ["x"]
    assert na.options_not_enforced(_schema("text", strict=True)) == []
