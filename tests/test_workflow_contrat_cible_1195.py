"""Le contrôle INVERSE du contrat d'une cible, lançable seul (#1195).

Ce que ce test fige :
- une SOURCE UNIQUE, `scripts/contrat-cible.sh` : la montée d'une cible
  (`deploy-cible.yml`) et le jugement seul d'un tag (`contrat-cible.yml`) l'appellent,
  et aucun des deux ne confronte ni ne lit un contrat par lui-même ;
- `contrat-cible.yml` juge un tag SANS RIEN MONTER : aucun script de déploiement, aucune
  machine ; il s'appelle depuis le dépôt du propriétaire et se lance ici à la main ;
- son tag est validé AVANT de servir de `ref:`, le code vient du tronc, aucune entrée
  n'est interpolée dans un script, les secrets lus sont ceux de la cible ;
- le verdict du script : compatible → 0 ; casse, contrat illisible, contrat non lu → 1, en
  le nommant ; la clé de lecture effacée quelle que soit l'issue.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import stat
import subprocess

import pytest
import yaml

_RACINE = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOWS = _RACINE / ".github" / "workflows"
_SCRIPT = _RACINE / "scripts" / "contrat-cible.sh"


def _wf(nom: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / nom).read_text(encoding="utf-8"))


_SEUL = _wf("contrat-cible.yml")
_DECLENCHEURS = _SEUL.get("on", _SEUL.get(True))      # PyYAML lit `on` comme True
_ETAPES = _SEUL["jobs"]["juger"]["steps"]


def _etapes_de(wf: dict) -> list[dict]:
    return [e for job in wf["jobs"].values() for e in job.get("steps", [])]


@pytest.mark.parametrize("nom", ["deploy-cible.yml", "contrat-cible.yml"])
def test_les_deux_chemins_passent_par_la_source_unique(nom):
    runs = [e.get("run", "") for e in _etapes_de(_wf(nom))]
    assert any("scripts/contrat-cible.sh consommateur" in r for r in runs), nom
    assert any('scripts/contrat-cible.sh juger "$RUNNER_TEMP/declaration.json"' in r
               for r in runs), nom
    for r in runs:
        assert "contrat-front.py" not in r and "lire-contrat-consommateur" not in r, nom


def test_juge_sans_rien_monter():
    texte = (_WORKFLOWS / "contrat-cible.yml").read_text(encoding="utf-8")
    for geste in ("appeler.sh", "constater.sh", "porte.sh", "deployer.sh", "ssh "):
        assert geste not in texte, geste
    assert set(_DECLENCHEURS) == {"workflow_call", "workflow_dispatch"}
    assert set(_DECLENCHEURS["workflow_call"]["inputs"]) == {"cible", "tag", "runner"}
    assert set(_DECLENCHEURS["workflow_call"]["secrets"]) == {
        "CIBLE_DECLARATION", "CIBLE_CONSOMMATEUR", "CIBLE_CONSOMMATEUR_CLE"}
    assert _SEUL["permissions"] == {"contents": "read"}


def test_le_tag_est_valide_avant_de_servir_de_ref_et_le_code_vient_du_tronc():
    noms = [e.get("name") or e.get("uses", "").split("@")[0] for e in _ETAPES]
    assert noms.index("Valider les entrées") < noms.index("actions/checkout")
    assert noms[0] == "Masquer la cible"
    checkout = next(e for e in _ETAPES if e.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"repository": "${{ env.TRONC }}",
                                "ref": "refs/tags/${{ inputs.tag }}",
                                "persist-credentials": False}
    assert _SEUL["env"]["TRONC"] == "otomata-tech/oto-backend"


def _valider(env: dict[str, str], tmp_path) -> subprocess.CompletedProcess:
    etape = next(e for e in _ETAPES if e.get("name") == "Valider les entrées")
    complet = {"CIBLE": "cible-exemple", "TAG": "v1.2.3", "CIBLE_DECLARATION": "{}", **env}
    return subprocess.run(["/bin/bash", "-c", etape["run"]],
                          env={"PATH": os.environ["PATH"],
                               "GITHUB_STEP_SUMMARY": str(tmp_path / "resume"), **complet},
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("env, refus", [
    ({}, None),
    ({"TAG": "main"}, "tag invalide"),
    ({"TAG": "v1.2.3; rm -rf /"}, "tag invalide"),
    ({"CIBLE": "Cible Exemple"}, "cible invalide"),
    ({"CIBLE_DECLARATION": ""}, "Déclaration absente"),
])
def test_les_entrees_sont_validees(tmp_path, env, refus):
    fini = _valider(env, tmp_path)
    if refus is None:
        assert fini.returncode == 0, fini.stdout + fini.stderr
    else:
        assert fini.returncode == 1 and refus in fini.stdout, fini.stdout


def test_aucune_entree_interpolee_et_les_secrets_sont_ceux_de_la_cible():
    texte = (_WORKFLOWS / "contrat-cible.yml").read_text(encoding="utf-8")
    for e in _ETAPES:
        assert "${{" not in e.get("run", ""), e.get("name")

    for v in set(re.findall(r"\$\{\{\s*(vars|secrets)\.([A-Z_]+)", texte)):
        assert v == ("secrets", v[1]) and v[1].startswith("CIBLE_"), v


# ── Le script ───────────────────────────────────────────────────────────────────────

def _consommateur(tmp_path, conso: str, verrou: bool) -> tuple[subprocess.CompletedProcess, str]:
    if verrou:
        (tmp_path / "uv.lock").write_text("")
    sortie = tmp_path / "sortie"
    sortie.write_text("")
    fini = subprocess.run([str(_SCRIPT), "consommateur"], cwd=tmp_path,
                          env={"PATH": os.environ["PATH"], "CONSOMMATEUR": conso,
                               "TAG": "v1.2.3", "GITHUB_OUTPUT": str(sortie)},
                          capture_output=True, text=True, timeout=30)
    return fini, sortie.read_text()


_CONSO = '{"nom": "front-exemple", "depot": "exemple-org/front-app", "chemin": "contrat/openapi.json", "cle": true}'


def test_sans_consommateur_rien_n_est_juge_et_c_est_dit(tmp_path):
    fini, sortie = _consommateur(tmp_path, "", verrou=False)
    assert fini.returncode == 0 and "Aucun consommateur déclaré" in fini.stdout
    assert sortie == "juger=non\n"


def test_un_consommateur_mal_declare_rougit(tmp_path):
    fini, sortie = _consommateur(tmp_path, '{"nom": "x"}', verrou=True)
    assert fini.returncode == 1 and "Consommateur mal déclaré" in fini.stdout
    assert "juger=oui" not in sortie


def test_un_tag_sans_verrou_rougit(tmp_path):
    fini, _ = _consommateur(tmp_path, _CONSO, verrou=False)
    assert fini.returncode == 1 and "Tag sans verrou" in fini.stdout


def test_un_consommateur_declare_est_juge(tmp_path):
    fini, sortie = _consommateur(tmp_path, _CONSO, verrou=True)
    assert fini.returncode == 0, fini.stdout + fini.stderr
    assert sortie.splitlines() == ["juger=oui", "nom=front-exemple", "depot=exemple-org/front-app",
                                   "chemin=contrat/openapi.json", "secret=CIBLE_CONSOMMATEUR_CLE"]


def _executable(chemin: pathlib.Path, texte: str) -> None:
    chemin.write_text(texte)
    chemin.chmod(chemin.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def arbre(tmp_path):
    """Un arbre de tag factice : les outils communs remplacés par des doublures pilotées
    par l'environnement, `python` (le jeu installé) par une doublure qui rend la
    facturation puis un contrat vide."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(_SCRIPT, tmp_path / "scripts" / "contrat-cible.sh")
    _executable(tmp_path / "scripts" / "lire-contrat-consommateur.sh", """#!/usr/bin/env bash
set -euo pipefail
TRAVAIL="$(mktemp -d -p "$BAC")"
echo cle > "$TRAVAIL/cle"
echo "$TRAVAIL" > "$BAC/travail-cree"
echo "travail=$TRAVAIL" >> "$GITHUB_OUTPUT"
echo "contrat=$TRAVAIL/contrat.json" >> "$GITHUB_OUTPUT"
echo "lu=$DOUBLURE_LU" >> "$GITHUB_OUTPUT"
""")
    (tmp_path / "scripts" / "contrat-front.py").write_text(
        "import os, sys\nsys.exit(int(os.environ['DOUBLURE_CODE']))\n")
    (tmp_path / "bin").mkdir()
    _executable(tmp_path / "bin" / "python", """#!/usr/bin/env bash
if grep -q env_inventory; then echo false; else echo '{}'; fi
""")
    (tmp_path / "bac").mkdir()
    (tmp_path / "declaration.json").write_text('{"roles": {}}')
    return tmp_path


def _juger(arbre, lu="oui", code=0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["scripts/contrat-cible.sh", "juger", str(arbre / "declaration.json")], cwd=arbre,
        env={"PATH": f"{arbre / 'bin'}:{os.environ['PATH']}", "TAG": "v1.2.3",
             "NOM": "front-exemple", "DEPOT": "exemple-org/front-app",
             "CHEMIN": "contrat/openapi.json", "SECRET": "CIBLE_CONSOMMATEUR_CLE",
             "CLE_LECTURE": "cle", "BAC": str(arbre / "bac"),
             "DOUBLURE_LU": lu, "DOUBLURE_CODE": str(code)},
        capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("lu, code, retour, message", [
    ("oui", 0, 0, None),
    ("oui", 1, 1, "v1.2.3 casserait front-exemple"),
    ("oui", 2, 1, "confrontation impossible"),
    ("non", 0, 1, "Contrat de front-exemple non jugé"),
])
def test_le_verdict_et_la_cle_effacee_quelle_que_soit_l_issue(arbre, lu, code, retour, message):
    fini = _juger(arbre, lu, code)
    assert fini.returncode == retour, fini.stdout + fini.stderr
    if message:
        assert message in fini.stdout, fini.stdout
    assert "contrat du tag :" in fini.stdout
    travail = pathlib.Path((arbre / "bac" / "travail-cree").read_text().strip())
    assert not travail.exists(), "la clé de lecture est restée sur le disque"
