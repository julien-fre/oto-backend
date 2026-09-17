"""Convoi du GIL (oto-backend#980, mesuré 16/09/2026) : `server.main()` resserre
l'intervalle de bascule des threads AVANT tout autre sous-système.

Constat mesuré sur la box, contre la vraie base, hors serveur : une lecture paginée
complète du datastore (8 910 lignes) prend 3,3 s seule, mais 15 à 24 s dès qu'un SEUL
thread de calcul tourne à côté avec l'intervalle par défaut (5 ms) — deux threads
Python se disputent le GIL bien plus que ne le justifie le travail réel. À 1 ms, la
lecture retombe à 8,1-8,4 s (le plancher : 0,5 ms n'apporte plus rien).

Ce banc ne rejoue pas la mesure de perf (coûteuse, contre une vraie base) : il prouve
le SEUL fait qui compte ici — que `main()` pose bien la valeur, et la pose TÔT, avant
qu'aucun thread applicatif (DB, sondes) n'ait pu partir avec le défaut. Lot minimal
(Alexis, 16/09) : ce seul réglage, rien de structurel.
"""
from __future__ import annotations

import sys

import pytest

from oto_mcp import server

_INTERVALLE_ATTENDU = 0.001


@pytest.fixture(autouse=True)
def _restaurer_intervalle():
    avant = sys.getswitchinterval()
    yield
    sys.setswitchinterval(avant)


def test_main_pose_lintervalle_avant_tout_autre_sous_systeme(monkeypatch):
    """`logging.basicConfig` est le tout premier appel après le réglage dans
    `main()` — le capturer AU MOMENT de son appel prouve que rien de plus lourd
    (Sentry, les boucles de fond, la base) n'a encore pu tourner avec le défaut."""
    capture: dict = {}

    def _capturer_puis_arreter(*a, **kw):
        capture["intervalle"] = sys.getswitchinterval()
        raise RuntimeError("arrêt volontaire du banc — rien après ce point ne doit tourner")

    monkeypatch.setattr("logging.basicConfig", _capturer_puis_arreter)

    with pytest.raises(RuntimeError, match="arrêt volontaire"):
        server.main()

    assert capture.get("intervalle") == _INTERVALLE_ATTENDU, (
        "l'intervalle de bascule n'est pas posé avant logging.basicConfig — "
        "retirer `sys.setswitchinterval` dans `main()` doit faire rougir CE test"
    )
