"""La tentative SANS base : ce qu'une donnée reçue devient avant d'atteindre le SQL.

Une valeur empoisonnée (charge libre, résultat déclaré par un worker) ne doit jamais
atteindre PostgreSQL sous une forme qu'il refuse : un refus du serveur fait avorter
TOUTE la transaction de l'appelant — la réservation d'un travail ou sa conclusion.
Le banc réel de ce contrat vit dans `test_runner_attempts_db.py` ; ici, la règle de
chaque conversion, cas par cas.
"""
from __future__ import annotations

import json

import pytest

from oto_mcp.db import runner_attempts as RA
from oto_mcp.db import runner_jobs


@pytest.mark.parametrize("valeur,attendu", [
    (12, 12), ("12", 12), (" 7 ", 7), (3.0, 3), (0, 0),
    (True, None), ("12k", None), ("²", None), (-1, None), (2**63, None),
    (float("nan"), None), (float("inf"), None), (2.5, None), (None, None), ({"a": 1}, None),
])
def test_un_entier_recu_devient_un_entier_ou_NULL(valeur, attendu):
    """`"²".isdigit()` est vrai et `int("²")` lève : le cas qui aurait fait tomber une
    conclusion sur un simple exposant."""
    assert RA._tentative_entier(valeur) == attendu


def test_le_nombre_d_etapes_tient_dans_la_colonne_INT():
    assert RA._tentative_entier(2**31, RA._TENTATIVE_INT_MAX) is None
    assert RA._tentative_entier(2**31 - 1, RA._TENTATIVE_INT_MAX) == 2**31 - 1


def test_un_texte_perd_ses_NUL_et_ses_surrogates_isoles():
    assert RA._tentative_texte("fin\x00") == "fin"
    assert RA._tentative_texte("\x00") is None
    RA._tentative_texte("x\ud800y").encode("utf-8")      # ne lève plus
    assert RA._tentative_texte(42) is None
    assert len(RA._tentative_texte("z" * 1000)) == RA._TENTATIVE_TEXTE_MAX


def _profonde(n: int):
    v: object = 1
    for _ in range(n):
        v = [v]
    return v


@pytest.mark.parametrize("valeur", [
    {"n": float("nan")}, {"n": float("inf")}, "pas un objet", 12, None,
    _profonde(RA._TENTATIVE_JSON_PROFONDEUR + 5), {"gros": "x" * (RA._TENTATIVE_JSON_MAX + 1)},
])
def test_une_couverture_impossible_a_stocker_devient_NULL(valeur):
    """NULL = non attestée : c'est ce qu'une couverture illisible EST."""
    assert RA._tentative_json(valeur) is None


def test_une_couverture_lisible_est_rendue_sans_rien_que_jsonb_refuse():
    texte = RA._tentative_json({"tours\x00": 2, "note": "a\x00b\ud800", "l": [1, "c\x00"]})
    assert "\\u0000" not in texte and "\x00" not in texte
    assert json.loads(texte)["tours"] == 2


def test_un_resultat_empoisonne_ne_garde_que_des_valeurs_inoffensives():
    lu = RA._tentative_resultat({
        "usage_input": "12k", "usage_output": 2**70, "usage_cache_read": -5,
        "usage_cache_write": True, "usage_input_total": "40",
        "steps": "abc", "stopped": "fin\x00", "model": {"x": 1},
        "usage_couverture": {"k": float("nan")}})
    assert [lu[p] for p in RA.POSTES_TENTATIVE] == [None, None, None, None, 40]
    assert (lu["steps"], lu["stopped"], lu["model"], lu["usage_couverture"]) == (
        None, "fin", None, None)


def test_un_resultat_qui_n_est_pas_un_objet_ne_leve_pas():
    lu = RA._tentative_resultat(["pas", "un", "objet"])
    assert all(lu[p] is None for p in RA.POSTES_TENTATIVE)


def test_le_complement_tardif_ne_remplit_que_ce_qui_est_NULL():
    actuel = {p: None for p in RA.POSTES_TENTATIVE} | {
        "usage_input": 10, "usage_couverture": None, "model": None}
    lu = {p: 7 for p in RA.POSTES_TENTATIVE} | {
        "usage_input": 999, "usage_couverture": '{"tours": 1}', "model": "claude-opus-5"}
    apport = RA._tentative_fusion(actuel, lu)
    assert "usage_input" not in apport, "un poste déjà connu ne s'écrase jamais"
    assert apport["usage_output"] == 7 and apport["usage_couverture"] == '{"tours": 1}'
    assert apport["model"] == "claude-opus-5"


def test_un_complement_qui_n_apporte_aucun_poste_ne_touche_pas_au_modele():
    actuel = {p: 1 for p in RA.POSTES_TENTATIVE} | {"usage_couverture": "{}", "model": None}
    assert RA._tentative_fusion(actuel, {p: 2 for p in RA.POSTES_TENTATIVE}
                                | {"usage_couverture": "{}", "model": "m"}) == {}


def test_un_complement_qui_n_apporte_que_la_couverture_n_ecrit_RIEN():
    """Contrat §1 : rien du tout si aucun poste n'est rempli. Une couverture posée seule
    attesterait des nombres qu'elle n'a pas apportés."""
    actuel = {p: 5 for p in RA.POSTES_TENTATIVE} | {"usage_couverture": None, "model": None}
    lu = {p: None for p in RA.POSTES_TENTATIVE} | {
        "usage_couverture": '{"tours": 1}', "model": "claude-opus-5"}
    assert RA._tentative_fusion(actuel, lu) == {}


@pytest.mark.parametrize("outcome,conclusible", [
    ("open", True), ("done", False), ("failed", False), ("lost", False)])
def test_seule_la_tentative_ouverte_se_conclut(outcome, conclusible):
    assert RA._tentative_conclusible(outcome) is conclusible


def test_la_source_parle_le_vocabulaire_de_la_file():
    """Un mot de plus ici (`hook`, …) ferait diverger « d'où vient la dépense » de
    « d'où vient le travail »."""
    for mot in runner_jobs._SOURCES:
        assert f"'{mot}'" in RA._TENTATIVE_SOURCE_SQL
    assert RA._TENTATIVE_SOURCE_SQL.count("'") == 2 * len(runner_jobs._SOURCES)


def test_une_ventilation_hors_de_la_table_fermee_est_refusee_avant_le_SQL():
    with pytest.raises(ValueError):
        RA.ventilation_des_tentatives(1, "org", "model; DROP TABLE runner_jobs", jours=30)


def test_un_regroupement_inconnu_est_refuse():
    with pytest.raises(ValueError):
        RA._tentative_portee("tout", 1)


def test_la_ligne_servie_ne_porte_ni_l_org_ni_la_machine():
    colonnes = RA._TENTATIVE_COLONNES_LIGNE
    assert "worker_sub" not in colonnes and "org_id" not in colonnes
