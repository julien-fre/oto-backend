"""Les routes déclarées lourdes (#1145) existent, et le débit ne coupe pas le relevé.

`ROUTES_LOURDES` vise une route par son GABARIT : un gabarit qui ne correspond à aucune
route servie ne borne rien, sans rien dire. On le confronte donc à la table figée des
routes. Et un débit posé sous le rythme d'un consommateur légitime casserait le
produit : le relevé de facturation d'un tenant fait ~17 lectures de `usage/calls` par
relevé, paginées, et a été observé jusqu'à 600 appels par minute.
"""
from __future__ import annotations

from pathlib import Path

from oto_mcp.api import routes_lourdes as rl

_TABLE = Path(__file__).with_name("api_routes_table.txt")


def _servies() -> set[tuple[str, str]]:
    servies = set()
    for ligne in _TABLE.read_text(encoding="utf-8").splitlines():
        methodes, _, reste = ligne.partition(" ")
        chemin = reste.split(" -> ")[0].strip()
        for m in methodes.split(","):
            servies.add((m, chemin))
    return servies


def test_chaque_route_lourde_est_une_route_servie():
    servies = _servies()
    for r in rl.ROUTES_LOURDES:
        assert (r.methode, r.gabarit) in servies, (
            f"{r.methode} {r.gabarit} : aucune route servie de ce gabarit — la borne "
            "ne viserait rien.")


def test_une_route_n_est_declaree_qu_une_fois():
    cles = [(r.methode, r.gabarit) for r in rl.ROUTES_LOURDES]
    assert len(cles) == len(set(cles))


def test_le_debit_du_releve_par_appel_ne_coupe_pas_le_releve_de_facturation():
    calls = next(r for r in rl.ROUTES_LOURDES if r.gabarit == "/api/orgs/{id}/usage/calls")
    assert calls.par_minute == 0 or calls.par_minute > 600
    assert calls.concurrence >= 4      # les 4 lectures parallèles d'un relevé passent


def test_les_routes_non_diagnostiquees_n_y_sont_pas():
    cles = {(r.methode, r.gabarit) for r in rl.ROUTES_LOURDES}
    assert ("GET", "/api/me/connectors") not in cles
    assert ("POST", "/api/me/runner/triggers") not in cles
