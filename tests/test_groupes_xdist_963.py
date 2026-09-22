"""Un module à fixture module-scopée ne se scinde pas entre deux workers xdist (#963).

Le rouge d'origine : `pytest -n 4` (dist `load`, découpe au niveau du TEST) envoyait deux
tests d'un même module sur deux workers ; la fixture `scope="module"` (une base Postgres
jetable) était instanciée deux fois, et le second test ne voyait pas ce que le premier
avait écrit. On rejoue ici la MÉCANIQUE, sans base : un module minuscule dont chaque test
consigne le worker qui l'exécute, lancé pour de vrai sous xdist.

Deux épreuves qui se répondent — sans le témoin, le vert ne prouverait rien :
  · `loadgroup` + le groupement de `_groupes_xdist` → UN seul worker pour le module ;
  · `load` (le défaut) sur le même module → PLUSIEURS workers (le mal, reproduit).
"""
from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("xdist")

RACINE = Path(__file__).resolve().parent.parent

CONFTEST = f"""
import sys
sys.path.insert(0, {str(RACINE / "tests")!r})
import _groupes_xdist

import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    _groupes_xdist.regrouper(items)
"""

MODULE_AVEC_FIXTURE = """
import os, time, pytest

@pytest.fixture(scope="module")
def etat():
    yield object()

@pytest.mark.parametrize("i", range(12))
def test_consigne_son_worker(etat, i):
    time.sleep(0.25)   # assez long pour que `load` répartisse sur plusieurs workers
    with open(os.environ["CARNET"], "a") as f:
        f.write(os.environ.get("PYTEST_XDIST_WORKER", "?") + "\\n")
"""

MODULE_SANS_FIXTURE = """
import os, time, pytest

@pytest.mark.parametrize("i", range(12))
def test_consigne_son_worker(i):
    time.sleep(0.25)
    with open(os.environ["CARNET"], "a") as f:
        f.write(os.environ.get("PYTEST_XDIST_WORKER", "?") + "\\n")
"""


def _workers(tmp_path: Path, module: str, dist: str) -> set[str]:
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_banc.py").write_text(textwrap.dedent(module))
    carnet = tmp_path / "carnet.txt"
    carnet.write_text("")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "3",
         "--dist", dist, str(tmp_path / "test_banc.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=180,
        env={**__import__("os").environ, "CARNET": str(carnet), "PYTHONPATH": ""})
    assert re.search(r"12 passed", r.stdout), r.stdout[-1500:] + r.stderr[-500:]
    return set(carnet.read_text().split())


def test_module_a_fixture_module_reste_sur_un_seul_worker(tmp_path):
    assert len(_workers(tmp_path, MODULE_AVEC_FIXTURE, "loadgroup")) == 1


def test_temoin_le_defaut_load_scinde_bien_le_module(tmp_path):
    """Sans quoi le test du dessus serait vert pour la mauvaise raison (un module trop
    petit pour être scindé) : c'est `load` qui produit le mal d'origine."""
    assert len(_workers(tmp_path, MODULE_AVEC_FIXTURE, "load")) > 1


def test_un_module_sans_fixture_module_reste_reparti_test_par_test(tmp_path):
    """Le groupement est CIBLÉ : pas de `loadfile` déguisé qui aplatirait l'équilibrage
    fin des ~600 fichiers sans fixture module-scopée."""
    assert len(_workers(tmp_path, MODULE_SANS_FIXTURE, "loadgroup")) > 1


def _lignes_pytest_parallele():
    for wf in sorted((RACINE / ".github" / "workflows").glob("*.yml")):
        for n, ligne in enumerate(wf.read_text().splitlines(), 1):
            if re.search(r"\bpytest\b.*\s-n\s", ligne) and not ligne.lstrip().startswith("#"):
                yield wf.name, n, ligne.strip()


def test_toute_commande_pytest_parallele_des_workflows_passe_loadgroup():
    """`xdist_group` est INERTE sans `--dist loadgroup` : un workflow qui lance `-n` sans
    lui reprend le flake sans qu'aucun test ne le dise."""
    lignes = list(_lignes_pytest_parallele())
    assert lignes, "aucun `pytest -n` dans les workflows — le banc ne garde plus rien"
    sans = [f"{wf}:{n}  {l}" for wf, n, l in lignes if "--dist loadgroup" not in l]
    assert not sans, "ces lancements `-n` n'ont pas `--dist loadgroup` (#963) :\n" + "\n".join(sans)
