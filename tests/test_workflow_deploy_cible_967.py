"""Le workflow d'une instance cible (`.github/workflows/deploy-cible.yml`, #967) : une
montée de version DÉCIDÉE, qui ne consulte que la cible et le tronc.

Ce que ce test fige, parce que chaque point est une décision (Alexis, 29/09/2026) :
- déclenché à la main seulement, avec un tag choisi : ici (`workflow_dispatch`), ou depuis
  le dépôt privé du propriétaire de la cible (`workflow_call`, décision du 30/09/2026) —
  mêmes entrées, secrets déclarés ; rien, dans ce dépôt, ne l'appelle ;
- le code exécuté vient du TRONC au tag, jamais du dépôt appelant ; l'environnement
  vérifié est celui du run (le dépôt appelant, quand il y en a un) ;
- l'accès à la machine est choisi explicitement (`acces` : tunnel | ssh), sans défaut ;
- il ne consulte AUCUN autre déploiement : ni un autre workflow, ni ses runs, ni une
  autre instance — ni dans le workflow, ni dans les scripts qu'il exécute ;
- l'environnement de la cible doit être protégé, vérifié avant l'approbation : il exige
  un relecteur, ou (dépôt privé sans relecteurs requis, décision du 30/09/2026) l'acteur
  du run figure dans sa liste de déclencheurs, vérifiée en PREMIER par le job qui le nomme ;
- un seul job derrière l'approbation : préprod d'abord, prod ensuite, qui exige que la
  préprod de la cible serve le tag ; le tag doit être sur la branche principale ;
- runner hébergé ; une entrée n'est jamais interpolée dans un script ;
- les valeurs d'une cible viennent de son environnement GitHub, jamais du dépôt.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess

import pytest
import yaml

_RACINE = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOWS = _RACINE / ".github" / "workflows"
_TEXTE = (_WORKFLOWS / "deploy-cible.yml").read_text(encoding="utf-8")
_WF = yaml.safe_load(_TEXTE)
_JOBS = _WF["jobs"]
_DECLENCHEURS = _WF.get("on", _WF.get(True))     # PyYAML lit `on` comme True
_ETAPES = _JOBS["monter"]["steps"]
_RUNS = [s.get("run", "") for s in _ETAPES]

# Ce qui désignerait un AUTRE déploiement que celui de la cible : nos workflows et leurs
# noms, l'API des runs, nos gardes de mise en production, nos hôtes et notre machine.
_AUTRE_CHAINE = re.compile(
    r"deploy\.yml|deploy-canari|release\.yml|Deploy (prod|preprod)|actions/runs|"
    r"garde_preprod|workflow_run|oto-platform|/opt/deploy|oto-backend\.sh|"
    r"oto\.cx|oto\.ninja|oto-mcp-canari|active-(prod|canari)")


_REFERENCE_DE_SCRIPT = re.compile(r"(?:deploy/cible|scripts)/[\w.-]+\.(?:sh|py)")


def _scripts_executes() -> list[pathlib.Path]:
    chemins = set(_REFERENCE_DE_SCRIPT.findall(_TEXTE))
    # la porte et le déploiement, que `appeler.sh` fait exécuter sur la machine
    chemins |= {"deploy/cible/porte.sh", "deploy/cible/deployer.sh"}
    # et, de proche en proche, ce que ces scripts exécutent à leur tour (#1195 : le
    # contrôle du contrat vit dans `scripts/contrat-cible.sh`, qui appelle les outils
    # communs de lecture et de confrontation)
    a_lire = list(chemins)
    while a_lire:
        script = _RACINE / a_lire.pop()
        for c in _REFERENCE_DE_SCRIPT.findall(_code(script)):
            if c not in chemins:
                chemins.add(c)
                a_lire.append(c)
    return sorted(_RACINE / c for c in chemins)


def _code(script: pathlib.Path) -> str:
    """Ce que le script EXÉCUTE : sans commentaires ni docstrings (un outil partagé peut
    raconter d'où il vient ; il ne doit pas y aller)."""
    texte = script.read_text(encoding="utf-8")
    if script.suffix == ".py":
        arbre = ast.parse(texte)
        for noeud in ast.walk(arbre):
            corps = getattr(noeud, "body", None)
            if (isinstance(corps, list) and corps and isinstance(corps[0], ast.Expr)
                    and isinstance(getattr(corps[0], "value", None), ast.Constant)
                    and isinstance(corps[0].value.value, str)):
                corps.pop(0)
        return ast.unparse(arbre)
    return "\n".join(l for l in texte.splitlines() if not l.lstrip().startswith("#"))


_ENTREES = {"cible", "tag", "etape", "action", "acces"}
_TRONC = "otomata-tech/oto-backend"


def test_declenche_a_la_main_ici_ou_par_le_depot_du_proprietaire():
    assert set(_DECLENCHEURS) == {"workflow_dispatch", "workflow_call"}
    for declencheur in _DECLENCHEURS.values():
        assert set(declencheur["inputs"]) == _ENTREES
        for nom in ("cible", "tag", "acces"):
            assert declencheur["inputs"][nom].get("required") is True, nom
        # l'accès se choisit : aucun défaut, donc aucun repli silencieux
        assert "default" not in declencheur["inputs"]["acces"]
    assert _DECLENCHEURS["workflow_dispatch"]["inputs"]["acces"]["options"] == ["tunnel", "ssh"]


def test_l_appel_declare_chaque_secret_qu_il_lit_sans_l_exiger():
    """Les secrets viennent de l'environnement de la cible dans le dépôt appelant (GitHub
    ne transmet pas un secret d'environnement par l'appel) : déclarés, jamais exigés à
    l'appel — c'est le script qui s'en sert qui exige chacun en le nommant."""
    declares = _DECLENCHEURS["workflow_call"]["secrets"]
    lus = set(re.findall(r"\$\{\{\s*secrets\.([A-Z_]+)", _TEXTE))
    assert set(declares) == lus
    assert lus >= {"CIBLE_DECLARATION", "CIBLE_CONSOMMATEUR", "CIBLE_SSH_HOTE",
                   "CIBLE_SSH_UTILISATEUR", "CIBLE_SSH_KNOWN_HOSTS", "CIBLE_SSH_CLE",
                   "CIBLE_CF_ACCESS_CLIENT_ID", "CIBLE_CF_ACCESS_CLIENT_SECRET",
                   "CIBLE_CONSOMMATEUR_CLE", "CIBLE_DECLENCHEURS"}
    assert all(d.get("required") is False for d in declares.values())


_ACTION_LOCALE = re.compile(r"\./\.github/actions/[\w-]+")
_EPINGLEE = re.compile(r"[\w-]+/[\w-]+@[0-9a-f]{40}")


def test_n_appelle_aucun_autre_workflow():
    """Appelable, il n'appelle rien : aucun job réutilisable, seules des actions épinglées.

    Une action LOCALE (`./.github/actions/…`, oto-backend#932) est du code du TRONC au
    tag — l'espace de travail est son checkout, fait avant elle — au même titre que les
    scripts `deploy/cible/` qu'il exécute ; ce qu'elle appelle à son tour est épinglé."""
    for nom, job in _JOBS.items():
        assert "uses" not in job, nom
        tronc_extrait = False
        for etape in job["steps"]:
            uses = etape.get("uses")
            if uses is None:
                continue
            tronc_extrait |= uses.startswith("actions/checkout@")
            if _ACTION_LOCALE.fullmatch(uses):
                assert tronc_extrait, f"{uses} lu avant le checkout du tronc"
                action = yaml.safe_load((_RACINE / uses / "action.yml").read_text(encoding="utf-8"))
                for sous in action["runs"]["steps"]:
                    if "uses" in sous:
                        assert _EPINGLEE.fullmatch(sous["uses"]), f"{uses} : {sous['uses']}"
                continue
            assert re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", uses), uses


def test_le_code_execute_vient_du_tronc_jamais_de_l_appelant():
    assert _WF["env"]["TRONC"] == _TRONC
    # la porte, sur la machine, écrit le même dépôt
    porte = (_RACINE / "deploy/cible/porte.sh").read_text(encoding="utf-8")
    assert f"DEPOT=https://github.com/{_TRONC}.git" in porte
    checkout = next(e for e in _ETAPES if "actions/checkout" in e.get("uses", ""))
    assert checkout["with"]["repository"] == "${{ env.TRONC }}"
    assert checkout["with"]["ref"] == "refs/tags/${{ inputs.tag }}"
    assert checkout["with"]["persist-credentials"] is False
    protection = next(e for e in _JOBS["entrees"]["steps"] if "protection.sh" in e.get("run", ""))
    assert '"https://github.com/${TRONC}.git"' in protection["run"]
    assert "${DEPOT}.git" not in protection["run"]
    # l'environnement vérifié est celui du run : le dépôt appelant, quand il y en a un
    assert protection["env"]["DEPOT"] == "${{ github.repository }}"


def _valider(env: dict[str, str], tmp_path) -> subprocess.CompletedProcess:
    etape = next(e for e in _JOBS["entrees"]["steps"] if e.get("name") == "Valider les entrées")
    complet = {"CIBLE": "cible-exemple", "TAG": "v1.2.3", "ETAPE": "preprod",
               "ACTION": "deployer", "ACCES": "ssh", **env}
    return subprocess.run(["/bin/bash", "-c", etape["run"]],
                          env={"PATH": os.environ["PATH"],
                               "GITHUB_STEP_SUMMARY": str(tmp_path / "resume"), **complet},
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("env, refus", [
    ({}, None),
    ({"ACCES": "tunnel"}, None),
    ({"ACCES": ""}, "acces invalide ou absent"),
    ({"ACCES": "direct"}, "acces invalide ou absent"),
    ({"ETAPE": "tout"}, "etape invalide"),
    ({"ACTION": "detruire"}, "action invalide"),
    ({"TAG": "main"}, "tag invalide"),
])
def test_les_entrees_sont_validees_meme_appelees(tmp_path, env, refus):
    """Appelé, rien ne borne les entrées (`workflow_call` n'a pas de `choice`) : le job
    `entrees` refuse toute valeur hors de ses listes, en la nommant."""
    fini = _valider(env, tmp_path)
    if refus is None:
        assert fini.returncode == 0, fini.stdout + fini.stderr
    else:
        assert fini.returncode == 1 and refus in fini.stdout, fini.stdout


def test_ne_consulte_aucun_autre_deploiement():
    assert not _AUTRE_CHAINE.search(_TEXTE), _AUTRE_CHAINE.search(_TEXTE)
    scripts = _scripts_executes()
    assert len(scripts) >= 6
    for script in scripts:
        code = _code(script)
        assert not _AUTRE_CHAINE.search(code), (script.name, _AUTRE_CHAINE.search(code))


def test_le_garde_fou_reconnait_une_reference_a_une_autre_chaine():
    """Le motif mord : un appel à notre garde de production serait vu."""
    assert _AUTRE_CHAINE.search('run: python3 scripts/garde_preprod_verte.py "$TAG"')
    assert _AUTRE_CHAINE.search("gh api repos/x/y/actions/runs?branch=main")


def test_rien_dans_ce_depot_ne_l_appelle():
    for f in _WORKFLOWS.glob("*.yml"):
        if f.name != "deploy-cible.yml":
            texte = f.read_text(encoding="utf-8")
            assert "deploy-cible" not in texte and "deploy/cible" not in texte, f.name


def test_aucun_runner_de_nos_machines():
    for nom, job in _JOBS.items():
        assert job["runs-on"] == "ubuntu-latest", nom


def test_une_montee_a_la_fois_par_cible():
    assert _WF["concurrency"]["group"] == "deploy-cible-${{ inputs.cible }}"
    assert _WF["concurrency"]["cancel-in-progress"] is False


def test_la_protection_est_verifiee_avant_l_approbation():
    assert "environment" not in _JOBS["entrees"]
    assert _JOBS["entrees"]["permissions"] == {"actions": "read"}
    etape = next(s for s in _JOBS["entrees"]["steps"]
                 if 'protection.sh environnement "$DEPOT" "$CIBLE"' in s.get("run", ""))
    assert _JOBS["entrees"]["outputs"]["protection"] == \
        f"${{{{ steps.{etape['id']}.outputs.protection }}}}"
    assert _JOBS["monter"]["needs"] == ["entrees"]


def test_la_decision_est_le_premier_controle_du_job_qui_nomme_l_environnement():
    """Seul ce job lit la liste des déclencheurs (secret de l'environnement) : il la
    vérifie avant tout geste — après le masquage et la lecture du tronc public, rien
    d'autre. Le verdict du premier temps arrive TOUJOURS (jamais de step sauté sur un
    verdict vide), et l'acteur est `triggering_actor` (celui qui relance décide)."""
    noms = [e.get("uses", "").split("@")[0] or e.get("run", "") for e in _ETAPES[:3]]
    assert "::add-mask::" in noms[0] and noms[1] == "actions/checkout"
    decision = _ETAPES[2]
    assert decision["run"] == "deploy/cible/protection.sh declencheurs"
    assert "if" not in decision
    assert decision["env"] == {"PROTECTION": "${{ needs.entrees.outputs.protection }}",
                               "ACTEUR": "${{ github.triggering_actor }}"}
    assert _JOBS["monter"]["env"]["CIBLE_DECLENCHEURS"] == "${{ secrets.CIBLE_DECLENCHEURS }}"
    assert "github.actor" not in _TEXTE


def test_un_seul_job_derriere_l_approbation():
    avec_env = [n for n, j in _JOBS.items() if "environment" in j]
    assert avec_env == ["monter"]
    assert _JOBS["monter"]["environment"] == "${{ inputs.cible }}"


def test_l_ordre_de_la_montee():
    def rang(fragment):
        return next(i for i, r in enumerate(_RUNS) if fragment in r)
    decision = rang("protection.sh declencheurs")
    tronc = rang("git merge-base --is-ancestor")
    declaration = rang("declaration.py verifier")
    contrat = rang("scripts/contrat-cible.sh juger")
    preprod = rang('appeler.sh "$ACTION" preprod "$TAG"')
    constat_preprod = rang('constater.sh "$RUNNER_TEMP/declaration.json" preprod "$TAG"')
    prod = rang('appeler.sh "$ACTION" prod "$TAG"')
    assert decision < tronc < declaration < contrat < preprod < constat_preprod < prod
    # le constat de la préprod garde la prod même quand on ne monte que la prod
    assert _ETAPES[constat_preprod]["if"] == "inputs.action == 'deployer'"


def test_une_entree_n_est_jamais_interpolee_dans_un_script():
    for nom, job in _JOBS.items():
        for etape in job.get("steps", []):
            assert "${{" not in etape.get("run", ""), (nom, etape.get("name"))


def test_les_valeurs_de_la_cible_viennent_de_son_environnement():
    for v in set(re.findall(r"\$\{\{\s*(vars|secrets)\.([A-Z_]+)", _TEXTE)):
        assert v[1].startswith("CIBLE_"), v
