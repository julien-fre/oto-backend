"""`scripts/instructions_en_anglais` : ne réécrit QUE ce que la plateforme a composé.

Une instruction écrite par quelqu'un est à lui, même en français : le plan la laisse
telle quelle. Une instruction composée en français — égale au caractère près à
l'ancienne composition des champs de SA ligne — reçoit la composition anglaise des
mêmes champs.
"""
from __future__ import annotations

import json

from oto_mcp.capabilities import _instruction
from scripts import instructions_en_anglais as S


def test_un_declencheur_compose_en_francais_est_reecrit_en_anglais():
    t = {"procedure": "veille", "input": S._derivee_fr("veille")}
    assert S.plan_declencheur(t) == _instruction.derivee("veille")


def test_une_instruction_ecrite_par_quelqu_un_n_est_jamais_touchee():
    ecrite = "Lis la procédure `veille` et applique-la, mais seulement le lundi."
    assert S.plan_declencheur({"procedure": "veille", "input": ecrite}) is None
    assert S.plan_campagne({"procedure": "veille", "namespace": "77",
                            "row_filter": None, "input": ecrite}) is None


def test_une_composition_d_une_AUTRE_procedure_n_est_pas_reecrite():
    """Égale à une composition, mais pas à celle des champs de sa ligne."""
    t = {"procedure": "veille", "input": S._derivee_fr("autre")}
    assert S.plan_declencheur(t) is None


def test_une_campagne_composee_est_reecrite_avec_sa_file_et_son_filtre():
    filtre = {"statut": "a_enrichir"}
    f = {"procedure": "enrichissement", "namespace": "77",
         "row_filter": json.dumps(filtre),       # relu en texte selon le pilote
         "input": S._de_file_fr("enrichissement", "77", filtre)}
    assert S.plan_campagne(f) == _instruction.de_file("enrichissement", "77", filtre)


def test_une_campagne_sans_file_retombe_sur_l_instruction_nue():
    f = {"procedure": "veille", "namespace": None, "row_filter": None,
         "input": S._derivee_fr("veille")}
    assert S.plan_campagne(f) == _instruction.derivee("veille")


def test_la_composition_servie_ne_porte_plus_le_marqueur_francais():
    """Sinon un second passage la reclasserait « à relire »."""
    assert S._MARQUEUR not in _instruction.derivee("veille")
    assert S._MARQUEUR not in _instruction.de_file("veille", "77", {"a": 1})


def test_un_filtre_a_plusieurs_cles_relu_de_jsonb_garde_l_ordre_cite():
    """jsonb rend les clés dans SON ordre ; la composition servie gardait celui reçu."""
    servi = {"statut": "a_enrichir", "pays": "FR"}
    relu = json.dumps({"pays": "FR", "statut": "a_enrichir"})
    f = {"procedure": "enrichissement", "namespace": "77", "row_filter": relu,
         "input": S._de_file_fr("enrichissement", "77", servi)}
    assert S.plan_campagne(f) == _instruction.de_file("enrichissement", "77", servi)


def test_un_filtre_cite_qui_n_egale_pas_celui_de_la_ligne_n_est_pas_reecrit():
    f = {"procedure": "enrichissement", "namespace": "77",
         "row_filter": json.dumps({"statut": "autre"}),
         "input": S._de_file_fr("enrichissement", "77", {"statut": "a_enrichir"})}
    assert S.plan_campagne(f) is None
