"""Le forçage du pin oto-core, éprouvé sur le montage RÉEL de pytest.

Un garde-fou qu'on n'a jamais vu parler n'atteste rien — et celui-ci existe
précisément parce qu'une doc juste n'avait pas suffi. On le fait donc diverger
pour de vrai (manifeste forgé, vrai `conftest.py`, vrai run pytest en
sous-processus), on lit la bannière, puis on remet les versions d'accord et on
vérifie qu'elle se TAIT. Les deux sens, sinon on n'aurait prouvé que la moitié :
une bannière qui s'affiche toujours ne dit rien de plus qu'une qui ne s'affiche
jamais.

⚠️ Le dépôt jouet **copie le vrai `conftest.py` et le vrai `_oto_core_pin.py`** :
c'est le montage servi qu'on exerce, hooks compris, pas une reconstitution en
mémoire de ce qu'ils sont censés faire.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from _oto_core_pin import (MARQUEUR, Ecart, ecart, lignes_de_banniere,
                           skips_autorises, tag_epingle)

RACINE = Path(__file__).resolve().parent.parent
TESTS = RACINE / "tests"

_LIGNE_PIN = ('  "oto-core[anonymize] @ '
              'git+https://github.com/otomata-tech/oto-core.git@{tag}",\n')


def _manifeste(*tags: str) -> str:
    lignes = "".join(_LIGNE_PIN.format(tag=t) for t in tags)
    return f"[project]\nname = \"jouet\"\ndependencies = [\n{lignes}]\n"


def _tag_installe() -> str:
    """Le tag git réellement installé, ou skip : sans lui, aucun des deux sens
    n'est mesurable (c'est le cas PyPI, traité par ailleurs)."""
    from oto_mcp.version import oto_core
    etat = oto_core()
    if etat.get("source") != "direct_url" or not etat.get("tag"):
        pytest.skip("oto-core n'est pas installé depuis git : écart non mesurable")
    return etat["tag"]


def _faux_depot(tmp_path: Path, *tags: str) -> Path:
    depot = tmp_path / "depot"
    (depot / "tests").mkdir(parents=True)
    (depot / "pyproject.toml").write_text(_manifeste(*tags), encoding="utf-8")
    for nom in ("conftest.py", "_oto_core_pin.py", "_pg_hygiene.py"):
        shutil.copy(TESTS / nom, depot / "tests" / nom)
    (depot / "tests" / "test_jouet.py").write_text(textwrap.dedent(f"""
        import pytest

        @pytest.mark.{MARQUEUR}
        def test_qui_n_a_de_sens_qu_au_pin():
            assert True

        def test_ordinaire():
            assert True
        """), encoding="utf-8")
    return depot


def _run(depot: Path) -> str:
    env = {**os.environ, "PYTHONPATH": str(RACINE)}
    env.pop("CI", None)          # on éprouve le comportement LOCAL
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:randomly"],
        cwd=depot, capture_output=True, text=True, env=env)
    return r.stdout + r.stderr


# --------------------------------------------------------------------------- #
# Les deux sens, sur le montage réel
# --------------------------------------------------------------------------- #

def test_la_banniere_parle_quand_le_venv_ne_suit_pas_le_pin(tmp_path):
    sortie = _run(_faux_depot(tmp_path, "v0.0.0-forge"))
    assert "PIN oto-core" in sortie, sortie
    # Le COUPLE, pas le mot « divergence » : c'est lui qui se comprend d'un coup.
    assert "v0.0.0-forge" in sortie, sortie
    assert _tag_installe() in sortie, sortie
    # Le test qui n'a de sens qu'au pin est NON CONCLUANT, pas rouge.
    assert "1 passed" in sortie and "1 skipped" in sortie, sortie


def test_la_banniere_se_tait_quand_les_versions_concordent(tmp_path):
    """Le cas NOMINAL — celui de la CI, et de tout venv à jour. Sans ce sens-là,
    on n'aurait qu'un bandeau permanent, c'est-à-dire un bandeau invisible."""
    sortie = _run(_faux_depot(tmp_path, _tag_installe()))
    assert "PIN oto-core" not in sortie, sortie
    assert "2 passed" in sortie, sortie


# --------------------------------------------------------------------------- #
# Ce sur quoi on refuse de crier
# --------------------------------------------------------------------------- #

def test_un_manifeste_a_deux_pins_ne_designe_pas_de_gagnant(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_text(_manifeste("v1.1.0", "v1.2.0"), encoding="utf-8")
    assert tag_epingle(m) is None


def test_un_manifeste_sans_pin_se_tait(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_text("[project]\ndependencies = []\n", encoding="utf-8")
    assert tag_epingle(m) is None
    assert ecart(pyproject=m) is None


def test_le_numero_gele_dune_install_pypi_ne_declenche_pas_dalarme(tmp_path):
    """`pip show` rend `1.100.0` quel que soit le tag : le comparer au pin
    fabriquerait une alarme PERMANENTE, donc fausse. On se tait."""
    m = tmp_path / "pyproject.toml"
    m.write_text(_manifeste("v1.103.0"), encoding="utf-8")
    gele = {"tag": "1.100.0", "commit": None, "source": "metadata"}
    assert ecart(pyproject=m, etat=gele) is None


def test_oto_core_absent_est_un_ecart_et_le_dit(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_text(_manifeste("v1.103.0"), encoding="utf-8")
    e = ecart(pyproject=m, etat={"tag": None, "commit": None, "source": "absent"})
    assert e == Ecart(None, "v1.103.0", "absent")
    assert "AUCUN" in "\n".join(lignes_de_banniere(e))


def test_la_concordance_ne_produit_aucun_ecart(tmp_path):
    m = tmp_path / "pyproject.toml"
    m.write_text(_manifeste("v1.103.0"), encoding="utf-8")
    au_pin = {"tag": "v1.103.0", "commit": "abc", "source": "direct_url"}
    assert ecart(pyproject=m, etat=au_pin) is None


# --------------------------------------------------------------------------- #
# La bannière elle-même
# --------------------------------------------------------------------------- #

def test_la_banniere_nomme_LES_DEUX_versions():
    texte = "\n".join(lignes_de_banniere(
        Ecart("v1.101.0", "v1.103.0", "direct_url")))
    assert "v1.101.0" in texte and "v1.103.0" in texte
    # Et elle dit où lire la recette, sans quoi on renvoie l'enquête à zéro.
    assert "docs/commands.md" in texte


def test_la_banniere_compte_les_tests_rendus_non_concluants():
    texte = "\n".join(lignes_de_banniere(
        Ecart("v1.101.0", "v1.103.0", "direct_url"), skips=28))
    assert "28 test(s) NON CONCLUANT(S)" in texte


def test_en_CI_aucun_test_ne_passe_sous_silence(monkeypatch):
    """L'asymétrie qui tient tout : le skip est un service EN LOCAL. En CI, la
    garde version-skew doit rester mordante — c'est là qu'elle protège la prod, et
    un skip y serait la panne muette qu'on prétend fermer."""
    monkeypatch.setenv("CI", "true")
    assert skips_autorises() is False
    monkeypatch.delenv("CI", raising=False)
    assert skips_autorises() is True
