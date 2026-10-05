"""Des options déclarées que le tableau ne fait pas encore respecter — et qui le disent
(#319, puis oto#124).

Signalé sur pièce par une mission : `options: ["oui","non","inconnu"]` posées sur un
tableau non-strict acceptaient « Peut-être » sans un mot. Le 05/10/2026 (oto#124) :
**toujours refuser, plus aucun réglage** — la liste s'applique sur TOUS les tableaux à
partir du 21/10/2026. D'ici là, la valeur hors liste est ÉCRITE avec un préavis daté
dans `notices` (`validation_complete`) ; le relevé d'écriture `hors_options` est
retiré, la pose le dit avec la date.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import schema as dsv2


ENUM = {"key": "priorite", "type": "enum", "options": ["haute", "basse"]}


def _preavis(schema, row, **kw):
    out: list = []
    errs = dsv2.validate_row(schema, row, preavis=out, **kw)
    return errs, out


# ── avant la date : la valeur passe, le préavis la nomme ─────────────────────

def test_a_value_outside_the_options_passes_with_a_dated_notice():
    errs, preavis = _preavis({"fields": [ENUM]}, {"priorite": "Moyenne"})
    assert errs == []
    assert preavis == ["priorite: valeur 'Moyenne' hors options (haute, basse)"]


def test_after_the_date_the_same_value_is_refused(validation_complete_partout):
    errs, preavis = _preavis({"fields": [ENUM]}, {"priorite": "Moyenne"})
    assert errs and "hors options (haute, basse)" in errs[0]
    assert preavis == [], "plus de préavis : la règle est en vigueur"


@pytest.mark.parametrize("declencheur", [
    {"fields": [ENUM, {"key": "x", "type": "text", "required": True}], "x": "ok"},
    {"fields": [ENUM, {"key": "x", "type": "text", "max_length": 10}]},
])
def test_an_armed_validation_already_refuses_and_says_no_notice(declencheur):
    """Une exigence arme la validation, qui juge déjà les options de tête : la valeur
    est refusée aujourd'hui, le préavis n'a rien à annoncer en plus."""
    schema = {"fields": declencheur["fields"]}
    row = {"priorite": "Moyenne", **({"x": "ok"} if "x" in declencheur else {})}
    errs, preavis = _preavis(schema, row)
    assert errs and preavis == []
    assert dsv2.options_not_enforced(schema) == []


def test_a_value_inside_the_options_says_nothing():
    assert _preavis({"fields": [ENUM]}, {"priorite": "haute"}) == ([], [])


def test_an_absent_field_is_not_a_violation():
    assert _preavis({"fields": [ENUM]}, {"autre": "x"}) == ([], [])


def test_a_value_written_in_LAYERS_is_unwrapped_before_being_judged():
    assert _preavis({"fields": [ENUM]},
                    {"priorite": {"valeur": "haute", "comment": "urgent"}}) == ([], [])


def test_a_LAYERED_value_really_outside_the_list_is_still_reported():
    _, preavis = _preavis({"fields": [ENUM]},
                          {"priorite": {"valeur": "Moyenne", "comment": "x"}})
    assert preavis and "'Moyenne'" in preavis[0], "la valeur, pas la structure"


@pytest.mark.parametrize("vide", ["", {"valeur": ""}, {"comment": "sans valeur"}])
def test_an_EMPTY_cell_is_not_a_value_outside_the_list(vide):
    assert _preavis({"fields": [ENUM]}, {"priorite": vide}) == ([], [])


def test_an_enum_without_options_condemns_nothing():
    schema = {"fields": [{"key": "p", "type": "enum"}]}
    assert _preavis(schema, {"p": "n'importe quoi"}) == ([], [])
    assert dsv2.options_not_enforced(schema) == []


# ── le pendant à la pose ─────────────────────────────────────────────────────

def test_posing_options_is_warned_with_the_date():
    champs = dsv2.options_not_enforced({"fields": [ENUM, {"key": "s", "type": "enum",
                                                          "options": ["a"]}]})
    assert champs == ["priorite", "s"]
    msg = dsv2.options_not_enforced_warning(champs)
    assert "appliquées à partir du 2026-10-21" in msg and "unknown_columns" not in msg


def test_after_the_date_nothing_is_said_at_pose(validation_complete_partout):
    assert dsv2.options_not_enforced({"fields": [ENUM]}) == []


def test_nothing_to_warn_gives_no_message():
    assert dsv2.options_not_enforced_warning([]) is None
    assert dsv2.json_depth_warning([]) is None


# ── le champ json (② du lot) ─────────────────────────────────────────────────

def test_a_json_field_is_flagged_at_pose_time():
    """Le fait est documenté mais invisible au moment de déclarer : une mission y
    avait mis toute sa traçabilité par champ avant de découvrir qu'elle n'était ni
    filtrable ni agrégeable."""
    champs = dsv2.json_fields_depth({"fields": [{"key": "provenance", "type": "json"},
                                                {"key": "nom", "type": "text"}]})
    assert champs == ["provenance"]

    msg = dsv2.json_depth_warning(champs)
    assert "premier niveau" in msg


def test_the_json_warning_states_the_fact_without_prescribing():
    """⚠️ Contrainte explicite du lot : énoncer le FAIT, sans recommander de
    contournement — la provenance native est en cours de conception, et conseiller une
    structure aujourd'hui reviendrait à prescrire ce qui sera obsolète demain."""
    msg = dsv2.json_depth_warning(["provenance"])

    for prescription in ("plutôt", "préfère", "utilise", "déclare", "remplace",
                         "aplatis", "colonne dédiée"):
        assert prescription not in msg.lower(), f"« {prescription} » prescrit un remède"


def test_json_is_found_in_depth():
    """Un champ `json` niché dans une fiche pose le même problème, moins visiblement."""
    champs = dsv2.json_fields_depth({"fields": [
        {"key": "occupant", "type": "object",
         "fields": [{"key": "meta", "type": "json"}]}]})
    assert champs == ["meta"]


# ── le vocabulaire reste DÉRIVÉ ──────────────────────────────────────────────

def test_the_detection_derives_from_the_functions_that_decide():
    """La détection de la pose suit `validation_active` — jamais une copie."""
    schema = {"fields": [ENUM, {"key": "livrable", "type": "text",
                                "required_when": {"priorite": "haute"}}]}
    assert dsv2.validation_active(schema) is True
    assert dsv2.options_not_enforced(schema) == []


# ── le vrai chemin d'écriture (PostgreSQL réel) ──────────────────────────────


def _store():
    from oto_mcp.datastore.core import make_store
    return make_store("sub-test")


def _table(st, schema):
    import uuid

    from oto_mcp import db
    ns = "t-" + uuid.uuid4().hex[:6]
    db.create_datastore("user", "sub-test", ns)
    out = st.set_schema(ns, schema)
    return ns, out


def test_the_real_write_path_carries_the_notice(live):
    """Le préavis remonte par le VRAI chemin (`_check_row`), dans `notices`, et la
    valeur est bel et bien écrite."""
    from oto_mcp import db
    st = _store()
    ns, _ = _table(st, {"fields": [ENUM]})

    st.append_row(ns, {"priorite": "Moyenne"})
    out = st.off_schema_report()

    assert "hors_options" not in out, "relevé retiré : le préavis le dit"
    notice = " ".join(out.get("notices") or [])
    assert "'Moyenne' hors options (haute, basse)" in notice
    assert "21 octobre 2026" in notice and "data_patch_schema" in notice
    ns_id = st.resolve_ns_id_for_write(ns)
    assert [l["data"]["priorite"] for l in db.datastore_list_rows(ns_id)] == ["Moyenne"]


def test_after_the_date_the_table_refuses(live, validation_complete_partout):
    from oto_mcp.datastore.core import RowValidationError
    st = _store()
    ns, _ = _table(st, {"fields": [ENUM]})
    with pytest.raises(RowValidationError):
        st.append_row(ns, {"priorite": "Moyenne"})


def test_posing_the_schema_warns_at_the_right_moment(live):
    st = _store()
    _, out = _table(st, {"fields": [ENUM, {"key": "prov", "type": "json"}]})
    w = out.get("warning") or ""
    assert "appliquées à partir du" in w, w
    assert "premier niveau" in w, "l'avertissement json doit être là aussi"


def test_a_clean_schema_says_nothing(live, validation_complete_partout):
    st = _store()
    _, out = _table(st, {"fields": [ENUM]})
    w = out.get("warning") or ""
    assert "appliquées à partir du" not in w and "premier niveau" not in w


def test_a_status_field_driven_by_a_lifecycle_is_not_a_false_positive():
    """Un champ porteur d'un `lifecycle` EST contraint (un état hors liste est refusé)
    — la pose ne l'annonce pas comme une liste à venir."""
    schema = {"fields": [
        {"key": "statut", "role": "status", "type": "enum", "options": ["a", "b"],
         "lifecycle": {"states": ["a", "b"]}},
        ENUM,
    ]}
    assert dsv2.validate_row(schema, {"statut": "zzz"})
    assert dsv2.validation_active(schema) is False
    assert dsv2.options_not_enforced(schema) == ["priorite"]
