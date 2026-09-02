"""Le garde-fou « référence poussée sans son objet » sait-il encore nommer le geste ?

Ces tests couvrent le point exact où la PREMIÈRE version du script se trompait : la
forme `from . import x`. C'est celle qui a cassé le tronc le 02/09/2026, et c'est la
seule des trois formes d'import dont l'exception ne porte PAS le nom du module absent
dans `.name` — classée naïvement, elle ressortait en « le module explose à l'import »
et renvoyait le lecteur chercher une panne d'environnement au lieu d'un `git add`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "arbre-importable.py"


def _charger():
    """Le script porte un tiret : il s'importe par son chemin, pas par son nom."""
    spec = importlib.util.spec_from_file_location("arbre_importable", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


garde = _charger()


def _paquet_jouet(racine: pathlib.Path) -> pathlib.Path:
    """Un paquet minuscule qui référence un module absent sous les trois formes."""
    pk = racine / "pk"
    pk.mkdir()
    (pk / "__init__.py").write_text("")
    (pk / "depuis_le_paquet.py").write_text("from . import absent\n")
    (pk / "depuis_le_module.py").write_text("from .absent import truc\n")
    (pk / "import_direct.py").write_text("import pk.absent\n")
    return pk


@pytest.mark.parametrize(
    "module",
    ["pk.depuis_le_paquet", "pk.depuis_le_module", "pk.import_direct"],
)
def test_les_trois_formes_d_import_nomment_le_module_absent(tmp_path, module, monkeypatch):
    """`from p import x` doit être reconnue comme les deux autres — c'est LA régression."""
    _paquet_jouet(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    for charge in [k for k in sys.modules if k == "pk" or k.startswith("pk.")]:
        del sys.modules[charge]

    with pytest.raises(ImportError) as leve:
        importlib.import_module(module)

    assert garde._objet_manquant(leve.value) == "pk.absent", (
        "l'objet manquant n'est pas nommé : le message renverra chercher une panne "
        "d'environnement au lieu du fichier oublié au commit"
    )


def test_une_exception_qui_n_est_pas_un_import_manquant_ne_se_fait_pas_passer_pour_un(tmp_path):
    """Sinon on accuserait un fichier oublié pour un module qui explose vraiment."""
    assert garde._objet_manquant(RuntimeError("base injoignable")) is None
    assert garde._objet_manquant(ImportError("message sans forme reconnue")) is None


def test_le_fichier_se_cherche_dans_LA_REF_jugee_pas_dans_le_checkout(tmp_path):
    """Éprouvé sur le vrai commit fautif : `git ls-files` répond pour HEAD, pas pour la ref.

    Le contrôle rougissait bien, mais annonçait « le fichier EST suivi par git » — vrai
    au moment de la mesure, faux dans le commit jugé. L'arbre matérialisé EST la ref.
    """
    arbre = tmp_path / "arbre"
    (arbre / "oto_mcp").mkdir(parents=True)
    (arbre / "oto_mcp" / "present.py").write_text("")
    assert garde._dans_le_commit("oto_mcp/present.py", arbre) is True
    assert garde._dans_le_commit("oto_mcp/absent.py", arbre) is False


def test_le_recit_nomme_le_geste_et_pas_seulement_l_erreur(tmp_path, monkeypatch):
    """« ImportError » renvoie chercher chez soi ; il faut dire `git add`."""
    _paquet_jouet(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(garde, "PAQUET", "pk")
    for charge in [k for k in sys.modules if k == "pk" or k.startswith("pk.")]:
        del sys.modules[charge]

    try:
        importlib.import_module("pk.depuis_le_paquet")
    except ImportError as exc:
        recit = garde.raconter(("pk.depuis_le_paquet", exc, exc.__traceback__), tmp_path)
    else:
        pytest.fail("l'import aurait dû échouer")

    assert "pk/absent.py" in recit
    assert "git add" in recit, "le message doit nommer le GESTE, pas seulement la panne"
    assert "PAS DANS CE COMMIT" in recit


def test_le_script_refuse_de_conclure_quand_il_n_a_rien_pu_juger(tmp_path):
    """Non négociable : jamais un vert faute d'avoir pu s'exécuter (sortie 2, pas 0)."""
    depot = tmp_path / "depot"
    depot.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=depot, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.test"], cwd=depot, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=depot, check=True)
    (depot / "rien.txt").write_text("aucun paquet servi ici\n")
    subprocess.run(["git", "add", "-A"], cwd=depot, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "sans paquet", "--no-verify"], cwd=depot, check=True
    )

    fait = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=depot, capture_output=True, text=True
    )
    assert fait.returncode == 2, (
        f"un arbre sans {garde.PAQUET}/ doit rendre « rien n'a été jugé », "
        f"jamais un succès muet (obtenu : {fait.returncode})"
    )
    assert "RIEN N'A ÉTÉ JUGÉ" in fait.stderr
