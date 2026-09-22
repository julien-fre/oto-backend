"""Lancer les fichiers racine ET les sous-dossiers de `tests/` en un appel collecte (#508).

`pytest tests/*.py tests/*/` glob aussi `tests/conftest.py` : sans garde, pytest l'importe
comme module de test, tombe sur `import file mismatch` (trois `conftest.py` partagent le
basename `conftest`) et interrompt TOUTE la collecte. La CI lance `pytest` sans chemin et
ne le voyait pas : un vert local et un vert CI qui ne lancent pas la même chose.

Ce banc rejoue le cas en petit (trois arguments, moins d'une seconde) plutôt que
d'exécuter la commande complète : c'est la COMBINAISON de conftest en argument qui casse,
pas le nombre de tests.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def _collecter(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=RACINE, capture_output=True, text=True, timeout=120)


def test_des_conftest_donnes_en_argument_ne_cassent_pas_la_collecte():
    r = _collecter("tests/conftest.py", "tests/db/conftest.py", "tests/test_file_source.py")
    assert r.returncode == 0, (
        "un `conftest.py` passé en argument (ce que fait `tests/*.py`) interrompt la "
        f"collecte :\n{r.stdout[-1500:]}")
    assert "import file mismatch" not in r.stdout + r.stderr


def test_un_conftest_en_argument_ne_retire_aucun_test():
    """Le nœud vide ne doit pas masquer le reste : mêmes tests avec ou sans conftest."""
    sans = _collecter("tests/test_file_source.py")
    avec = _collecter("tests/conftest.py", "tests/db/conftest.py", "tests/test_file_source.py")
    assert sans.returncode == 0 and avec.returncode == 0
    ids = lambda r: {l for l in r.stdout.splitlines() if "::" in l}  # noqa: E731
    assert ids(sans) and ids(sans) == ids(avec)
