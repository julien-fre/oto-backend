"""La loi du tirage des campagnes : laquelle est tentée, et avec quelle chance.

Sans base : ce qui est en cause ici est une loi de probabilité, et elle se mesure sur
beaucoup de tirages. Le compte des files, lui, s'éprouve en SQL réel
(`test_lignes_reservables_db.py`).
"""
from __future__ import annotations

import collections
import random

import pytest

from oto_mcp.capabilities import _ordre_de_service as O


def test_une_file_vide_et_un_compte_echoue_ne_sont_pas_tentes():
    candidates = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    comptes = {1: 5, 2: 0, 4: None}          # 2 : file vide ; 3 : compte échoué

    ordre = O.ordonner(candidates, compter=lambda _c: comptes, rng=random.Random(1))

    assert sorted(ordre) == [1, 4]


def test_les_probabilites_suivent_la_formule():
    p = O.probabilites({1: 171, 2: 9, 3: 2}, plancher=0.2)

    assert sum(p.values()) == pytest.approx(1)
    assert p[1] == pytest.approx(0.2 / 3 + 0.8 * 171 / 182)
    assert p[3] == pytest.approx(0.2 / 3 + 0.8 * 2 / 182)


def test_le_plancher_garde_sa_chance_a_la_plus_petite_file():
    p = O.probabilites({1: 1, **{i: 10_000 for i in range(2, 11)}})

    assert p[1] >= O.PLANCHER / 10


def test_sans_aucune_ligne_comptee_le_tirage_est_egal():
    assert O.probabilites({1: None, 2: None}) == {1: 0.5, 2: 0.5}


def test_une_campagne_sans_tableau_ne_pese_que_par_le_plancher():
    assert O.probabilites({1: None, 2: 100})[1] == pytest.approx(O.PLANCHER / 2)


def test_la_premiere_place_suit_la_loi_sur_un_grand_nombre_de_tirages():
    """Le tirage sans remise doit rendre la loi annoncée, pas une approximation."""
    rng = random.Random(20260914)
    probas = O.probabilites({1: 171, 2: 9, 3: 2, 4: 17, 5: 44})
    n = 40_000

    premiers = collections.Counter(O.ordre_pondere(probas, rng)[0] for _ in range(n))

    for fid, p in probas.items():
        assert premiers[fid] / n == pytest.approx(p, abs=0.01), fid


def test_l_ordre_est_une_permutation_des_servables():
    probas = O.probabilites({1: 3, 2: None, 3: 50})
    assert sorted(O.ordre_pondere(probas, random.Random(7))) == [1, 2, 3]


@pytest.mark.parametrize("plancher", [0, -0.1, 1.5])
def test_un_plancher_hors_bornes_est_refuse(plancher):
    with pytest.raises(ValueError):
        O.probabilites({1: 1}, plancher=plancher)
