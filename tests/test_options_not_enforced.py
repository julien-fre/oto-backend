"""Des options déclarées hors régime strict ne contraignent rien — et le disent (#319).

Signalé sur pièce par une mission : `options: ["oui","non","inconnu"]` posées sur un
tableau non-strict acceptent « Peut-être » sans un mot. `validation_active` ne s'arme
que sur `strict` / `required` / `required_when` / `max_length`.

⚠️ Le défaut était **aggravé par #316**, dont l'avertissement dirige vers `options` —
donc vers une clé qui, hors strict, ne contraint rien. Le correctif précédent avait
déplacé le mensonge d'un cran ; celui-ci le referme.

**On avertit, on ne refuse pas** : un tableau non-strict est en régime souple PAR
DÉCLARATION. Mesuré en production — 23 tableaux sur 57 sont dans ce cas, et les 118
valeurs réellement hors liste sont toutes sur un seul, dont les écritures deviendraient
des erreurs du jour au lendemain. Le régime strict, lui, refuse déjà.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import schema as dsv2


ENUM = {"key": "priorite", "type": "enum", "options": ["haute", "basse"]}


# ── le silence est levé, l'écriture passe ────────────────────────────────────

def test_a_value_outside_the_options_is_reported_when_nothing_enforces_it():
    schema = {"fields": [ENUM]}
    hors = dsv2.unenforced_options(schema, {"priorite": "Moyenne"})

    assert hors == {"priorite": "Moyenne"}
    msg = dsv2.unenforced_options_warning(hors)
    # La CONSÉQUENCE avant le remède : sans elle on lit « valeur inhabituelle » là où
    # il faut lire « ce champ n'est pas la liste fermée que le schéma laisse croire ».
    assert "ÉCRITE quand même" in msg
    assert "strict: true" in msg


@pytest.mark.parametrize("declencheur", [
    {"strict": True},
    {"fields": [ENUM, {"key": "x", "type": "text", "required": True}]},
    {"fields": [ENUM, {"key": "x", "type": "text", "max_length": 10}]},
])
def test_nothing_is_reported_when_the_validation_is_armed(declencheur):
    """Dès que la validation est armée, la valeur est REFUSÉE en amont : le redire
    serait un doublon bavard sur un chemin qui ne peut pas passer.

    Les trois déclencheurs sont couverts parce que c'est la LISTE de
    `validation_active` qui décide — pas une copie de sa logique. Si `options` y entre
    un jour, ces avertissements s'éteignent d'eux-mêmes."""
    schema = {"fields": [ENUM], **declencheur}
    assert dsv2.unenforced_options(schema, {"priorite": "Moyenne"}) == {}
    assert dsv2.options_not_enforced(schema) == []


def test_a_value_inside_the_options_says_nothing():
    assert dsv2.unenforced_options({"fields": [ENUM]}, {"priorite": "haute"}) == {}
    assert dsv2.unenforced_options_warning({}) is None


def test_an_absent_field_is_not_a_violation():
    """Ne rien écrire dans un champ n'est pas écrire hors liste — sans quoi tout
    geste partiel déclencherait l'avertissement."""
    assert dsv2.unenforced_options({"fields": [ENUM]}, {"autre": "x"}) == {}


# ── ce que l'avertissement doit JUGER : la valeur, et seulement si elle existe ──

def test_a_value_written_in_LAYERS_is_unwrapped_before_being_judged():
    """⚠️ Une cellule vaut `{"valeur": …, "comment": …}` dès qu'un agent la justifie
    en couches — geste NORMAL, et recommandé. Comparée à sa liste SANS déballage, elle
    est fatalement « hors options » : le repr d'un dict n'est jamais une option.

    L'avertissement criait donc à tort sur une valeur parfaitement légitime, et citait
    la structure Python au lieu de la valeur. Le chemin de REFUS (régime strict)
    déballait déjà depuis toujours ; seul l'avertissement ne le faisait pas.

    ⚠️ **Mesuré avant de corriger** : 0 cellule en couches sur les 22 331 cellules
    pleines des 151 tableaux souples à options de la production (09/09/2026). Le trou
    n'était atteint par rien — ce qui rend le correctif gratuit, pas inutile : il ferme
    la porte avant qu'on la pousse."""
    schema = {"fields": [ENUM]}
    assert dsv2.unenforced_options(
        schema, {"priorite": {"valeur": "haute", "comment": "urgent"}}) == {}


def test_a_LAYERED_value_really_outside_the_list_is_still_reported():
    """Déballer ne veut pas dire fermer les yeux : c'est la VALEUR qu'on juge, et le
    relevé la cite ELLE, jamais son enveloppe — sinon le message reste illisible."""
    hors = dsv2.unenforced_options(
        {"fields": [ENUM]}, {"priorite": {"valeur": "Moyenne", "comment": "x"}})
    assert hors == {"priorite": "Moyenne"}, "la valeur, pas la structure"


@pytest.mark.parametrize("vide", ["", "   ", {"valeur": ""}, {"comment": "sans valeur"}])
def test_an_EMPTY_cell_is_not_a_value_outside_the_list(vide):
    """⚠️ Une cellule vide n'a aucune valeur fautive à corriger. « valeur hors des
    options déclarées : `status` = '' — elle est ÉCRITE quand même » envoie chercher
    ce qui n'existe pas, et ce qu'un geste a vidé est déjà dit — mieux — par
    `off_erased` / `off_ignored`.

    Mesuré sur du vivant, celui-là : **25 écritures** l'ont déclenché à tort, sur 4
    tableaux et 3 propriétaires. Même règle que `etats_trahis`, qui ignore `None` et
    la chaîne vide depuis sa première ligne."""
    assert dsv2.unenforced_options({"fields": [ENUM]}, {"priorite": vide}) == {}


def test_an_enum_without_options_condemns_nothing():
    """Un enum sans liste est un enum LIBRE : il ne promet rien, donc ne ment pas."""
    schema = {"fields": [{"key": "p", "type": "enum"}]}
    assert dsv2.unenforced_options(schema, {"p": "n'importe quoi"}) == {}
    assert dsv2.options_not_enforced(schema) == []


# ── le pendant à la pose ─────────────────────────────────────────────────────

def test_posing_options_without_strict_is_warned_at_the_right_moment():
    """Le moment qui compte est celui où l'on ÉCRIT le schéma — pas six semaines plus
    tard devant des valeurs libres. C'est le pendant exact de #316."""
    champs = dsv2.options_not_enforced({"fields": [ENUM, {"key": "s", "type": "enum",
                                                          "options": ["a"]}]})
    assert champs == ["priorite", "s"]

    msg = dsv2.options_not_enforced_warning(champs)
    assert "NON appliquées" in msg and "strict: true" in msg


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
    """⚠️ Ce lot existe parce qu'une liste avait divergé de ce que le code lit. Sa
    propre détection ne doit donc pas recopier la logique de `validation_active` : on
    le vérifie en armant la validation par un chemin que le lot ne connaît pas
    explicitement — un `required_when` niché dans un champ quelconque."""
    schema = {"fields": [ENUM, {"key": "livrable", "type": "text",
                                "required_when": {"priorite": "haute"}}]}

    assert dsv2.validation_active(schema) is True
    assert dsv2.unenforced_options(schema, {"priorite": "Moyenne"}) == {}
    assert dsv2.options_not_enforced(schema) == []


# ── le vrai chemin d'écriture (PostgreSQL réel) ──────────────────────────────


def _store():
    from oto_mcp.datastore.core import make_store
    return make_store("sub-test")


def test_the_real_write_path_carries_the_warning(live):
    """⚠️ Ce qui compte pour l'utilisateur : l'avertissement remonte par le VRAI
    chemin (`_check_row`, le seam que tous les gestes traversent), pas seulement
    depuis les fonctions pures. Sans ce test, le lot pourrait être vert et muet en
    conditions réelles."""
    import uuid

    from oto_mcp import db
    st = _store()
    ns = "t-" + uuid.uuid4().hex[:6]
    db.create_datastore("user", "sub-test", ns)
    st.set_schema(ns, {"fields": [ENUM]})           # options, PAS de strict

    st.append_row(ns, {"priorite": "Moyenne"})
    out = st.off_schema_report()

    assert out.get("hors_options") == {"priorite": "Moyenne"}
    assert "ÉCRITE quand même" in (out.get("hors_options_hint") or "")
    # …et la valeur est bel et bien écrite : on avertit, on ne refuse pas.
    ns_id = st.resolve_ns_id_for_write(ns)
    lignes = db.datastore_list_rows(ns_id)
    assert [l["data"]["priorite"] for l in lignes] == ["Moyenne"]


def test_the_strict_table_still_refuses(live):
    """Le régime strict est inchangé — c'est lui qui protège les tableaux qui l'ont
    demandé, et le lot ne doit pas l'avoir attendri."""
    import uuid

    from oto_mcp import db
    from oto_mcp.datastore.core import RowValidationError
    st = _store()
    ns = "t-" + uuid.uuid4().hex[:6]
    db.create_datastore("user", "sub-test", ns)
    st.set_schema(ns, {"fields": [ENUM], "strict": True})

    with pytest.raises(RowValidationError):
        st.append_row(ns, {"priorite": "Moyenne"})


def test_posing_the_schema_warns_at_the_right_moment(live):
    """L'avertissement de POSE, par la vraie surface."""
    import uuid

    from oto_mcp import db
    st = _store()
    ns = "t-" + uuid.uuid4().hex[:6]
    db.create_datastore("user", "sub-test", ns)

    out = st.set_schema(ns, {"fields": [ENUM, {"key": "prov", "type": "json"}]})

    w = out.get("warning") or ""
    assert "NON appliquées" in w, w
    assert "premier niveau" in w, "l'avertissement json doit être là aussi"


def test_a_clean_schema_says_nothing(live):
    """Pas de bruit sur le cas normal : un schéma strict et sans json ne déclenche
    aucun de ces deux avertissements."""
    import uuid

    from oto_mcp import db
    st = _store()
    ns = "t-" + uuid.uuid4().hex[:6]
    db.create_datastore("user", "sub-test", ns)

    out = st.set_schema(ns, {"fields": [ENUM], "strict": True})

    w = out.get("warning") or ""
    assert "NON appliquées" not in w and "premier niveau" not in w


def test_a_status_field_driven_by_a_lifecycle_is_not_a_false_positive():
    """⚠️ **Le faux positif que ce lot a failli introduire**, attrapé par un test
    voisin (`test_datastore_queue_release_warning`).

    Un champ `role="status"` porteur d'un `lifecycle` EST contraint : un état hors
    liste est refusé même quand `validation_active` est faux. L'avertir aurait été
    faux — et un avertissement qui crie à tort est celui qu'on apprend à ignorer,
    donc celui qui ruine les deux autres du lot.

    L'exclusion est DÉRIVÉE de `lifecycle_of`/`status_field` : le mécanisme de cycle
    de vie est en cours de retrait (#317), et elle s'éteindra d'elle-même le jour où
    il partira — sans que personne ait à y penser."""
    schema = {"fields": [
        {"key": "statut", "role": "status", "type": "enum", "options": ["a", "b"],
         "lifecycle": {"states": ["a", "b"]}},
        ENUM,                                    # celui-là n'est contraint par rien
    ]}

    # Le lifecycle refuse bien, sans strict — c'est ce qui fait le faux positif.
    assert dsv2.validate_row(schema, {"statut": "zzz"})
    assert dsv2.validation_active(schema) is False

    # …donc le statut est TU, et la priorité seule est signalée.
    assert dsv2.options_not_enforced(schema) == ["priorite"]
    assert dsv2.unenforced_options(
        schema, {"statut": "zzz", "priorite": "Moyenne"}) == {"priorite": "Moyenne"}
