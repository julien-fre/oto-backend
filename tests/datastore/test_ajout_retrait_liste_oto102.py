"""`tags[+]` (un ou plusieurs éléments) et `tags[-]` (retrait par valeur), oto#102 — la
grammaire et la résolution, sans base. Le banc vivant
(`test_ajout_retrait_liste_oto102_live.py`) éprouve la concurrence, les faces et le
journal ; celui-ci tient ce que la CI rejoue sans PostgreSQL."""
from __future__ import annotations

import pytest

from oto_mcp.datastore.errors import RowValidationError
from oto_mcp.datastore.rangs import sortir_les_rangs

SCHEMA = {"fields": [
    {"key": "tags", "type": "list", "of": {"type": "text"}},
    {"key": "contacts", "type": "list", "of": {"type": "object", "fields": [
        {"key": "nom", "type": "text"}]}},
]}


def _geste(row: dict, en_place: dict, *, creation: bool = False):
    reste, rangs = sortir_les_rangs(SCHEMA, row)
    rangs.preparer(SCHEMA, lambda _s, d: d)
    return reste, rangs, rangs.appliquer(en_place, SCHEMA, creation=creation)


def _refus(geste) -> str:
    with pytest.raises(RowValidationError) as exc:
        geste()
    return " ".join(exc.value.errors)


def test_une_liste_s_ajoute_dans_l_ordre_et_chaque_ajout_est_juge():
    _r, rangs, out = _geste({"tags[+]": ["c", "a"]}, {"tags": ["a", "b"]})
    assert out == {"tags": ["a", "b", "c", "a"]}
    assert rangs.ecrits == {"tags": {2, 3}}


def test_un_element_seul_s_ajoute_comme_avant():
    _r, rangs, out = _geste({"contacts[+]": {"nom": "Cy"}}, {"contacts": [{"nom": "A"}]})
    assert out == {"contacts": [{"nom": "A"}, {"nom": "Cy"}]}
    assert rangs.ecrits == {"contacts": {1}}


def test_le_retrait_ote_toutes_les_occurrences_puis_l_ajout_suit():
    _r, rangs, out = _geste({"tags[-]": "a", "tags[+]": "d"}, {"tags": ["a", "b", "a"]})
    assert out == {"tags": ["b", "d"]}
    assert rangs.ecrits == {"tags": {1}}


def test_les_rangs_ecrits_se_relisent_apres_retraits_et_suppressions():
    en_place = {"contacts": [{"nom": "A"}, {"nom": "B"}, {"nom": "C"}]}
    _r, rangs, out = _geste({"contacts[0]": None, "contacts[2].nom": "C2"}, en_place)
    assert out == {"contacts": [{"nom": "B"}, {"nom": "C2"}]}
    assert rangs.ecrits == {"contacts": {1}}


def test_tout_retirer_efface_la_colonne():
    _r, _rangs, out = _geste({"tags[-]": ["a", "b"]}, {"tags": ["a", "b"]})
    assert out == {"tags": None}


def test_l_egalite_est_celle_du_json():
    _r, _rangs, out = _geste({"tags[-]": [True, 1]}, {"tags": [1, True, 1.0, "1"]})
    assert out == {"tags": ["1"]}


def test_une_valeur_absente_est_refusee_en_la_nommant():
    texte = _refus(lambda: _geste({"tags[-]": ["a", "zz"]}, {"tags": ["a"]}))
    assert '`"zz"`' in texte and "1 élément en place" in texte
    texte = _refus(lambda: _geste({"tags[-]": "a"}, {}, creation=True))
    assert "CRÉE la ligne" in texte


@pytest.mark.parametrize("row, attendu", [
    ({"tags[+]": None}, "`null`"),
    ({"tags[+]": []}, "une liste vide"),
    ({"tags[-]": ["a", None]}, "un `null` dans la liste"),
    ({"contacts[-]": "A"}, "`\"contacts[<rang>]\": null`"),
    ({"tags[-]": {"nom": "A"}}, "liste de VALEURS"),
    ({"tags[-].x": "a"}, "liste de VALEURS"),
    ({"tags[-]": "a", "tags": ["b"]}, "ENTIÈRE et par rang"),
])
def test_les_formes_sans_objet_sont_refusees(row, attendu):
    assert attendu in _refus(lambda: sortir_les_rangs(SCHEMA, row))


def test_un_ajout_ne_peut_pas_fabriquer_une_identite_double():
    schema = {"fields": [{"key": "contacts", "type": "list",
                          "of": {"type": "object", "key": "role",
                                 "fields": [{"key": "role", "type": "text"}]}}]}
    _reste, rangs = sortir_les_rangs(schema, {"contacts[+]": [{"role": "RH"},
                                                              {"role": "RH"}]})
    rangs.preparer(schema, lambda _s, d: d)
    assert "en double" in _refus(lambda: rangs.appliquer({}, schema, creation=True))


def test_un_mot_reserve_dans_une_liste_de_valeurs_reste_refuse():
    _reste, rangs = sortir_les_rangs(SCHEMA, {"tags[+]": ["a", "@empty"]})
    assert "tags[+][1]" in _refus(lambda: rangs.preparer(SCHEMA, lambda _s, d: d))


def test_les_noms_a_crochets_hors_grammaire_restent_des_colonnes():
    reste, rangs = sortir_les_rangs(None, {"Prix [EUR]": 3, "note[a]": 1, "x[-1]": 2})
    assert rangs is None and reste == {"Prix [EUR]": 3, "note[a]": 1, "x[-1]": 2}


# ── compter par élément : la lecture de l'élément nu ────────────────────────────

def test_l_element_nu_est_un_chemin_de_liste():
    from oto_mcp.db.paths import field_read_sql, split_list_path
    assert split_list_path("tags[]") == ("tags", None, None)
    assert split_list_path("tags[1]") == ("tags", 1, None)
    assert split_list_path("Note [1]") is None
    with pytest.raises(ValueError, match="TOUS les items"):
        field_read_sql("tags[]")
    sql, params = field_read_sql("tags[1]")
    assert "data->%s->1 #>> '{}'" in sql and set(params) == {"tags"}


def test_l_agregat_deroule_l_element_nu():
    from oto_mcp.db.query import _build_aggregate
    sql, params, names = _build_aggregate(
        1, "tags[]", [{"op": "count"}, {"op": "count_rows"}], None, None, 100)
    assert "LATERAL jsonb_array_elements" in sql and "_el.v #>> '{}'" in sql
    assert names == [("m0", "count"), ("m1", "count_rows")]
    sql, _p, _n = _build_aggregate(
        1, None, None, None, [{"field": "tags[]", "op": "eq", "value": "x"}], 100)
    assert "EXISTS (SELECT 1 FROM jsonb_array_elements" in sql
