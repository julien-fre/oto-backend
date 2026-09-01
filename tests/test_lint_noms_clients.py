"""Le garde-fou anti-nom-de-client, et la preuve qu'il MORD.

`scripts/lint_noms_clients.py` refuse un nom de client, de personne réelle ou de
domaine client dans ce dépôt PUBLIC. Il est né du 2026-09-01 : la règle avait été
appliquée à la main deux fois le matin (#709 sources, #747 tests, tronc à zéro) et
un lot mergé à 10:26 la cassait le jour même. Une règle qui ne tient que par la
discipline ne tient pas.

⚠️ **Un garde-fou d'inventaire se prouve en lui présentant l'anomalie qu'il
prétend attraper** (convention du repo, née de trois bancs qui mentaient par
omission en août). D'où les cas synthétiques ci-dessous.

⚠️ **Les termes employés ici sont FICTIFS**, et c'est structurel : écrire un vrai
nom de client dans ce fichier publierait exactement ce que le garde-fou existe
pour cacher. La vraie liste vit hors du dépôt (cf. la docstring du script) ; ce
banc prouve le MÉCANISME, la CI le confronte au vrai tronc avec la vraie liste.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from scripts.lint_noms_clients import (  # noqa: E402
    charger_termes, compiler, scanner)

# Purement fictifs — aucun rapport avec un client réel.
TERMES = ["zorglub", "wayne-enterprises", "Bruce Wayne", "zorglub.example"]


def _depot(tmp_path: pathlib.Path, fichiers: dict[str, str]) -> pathlib.Path:
    """Un vrai dépôt git : le scanner énumère par `git ls-files`, pas par glob —
    le prouver sur un faux répertoire ne prouverait pas ce qui tourne."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel, contenu in fichiers.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contenu, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _scan(tmp_path, fichiers: dict[str, str]) -> list:
    return list(scanner(_depot(tmp_path, fichiers), TERMES))


# ── ce qu'il DOIT attraper ───────────────────────────────────────────────────

@pytest.mark.parametrize("rel,contenu", [
    ("mod.py", '"""En-tête de module citant zorglub."""'),
    ("tests/test_x.py", "# cf. zorglub-bridge, derrière le même auth"),
    ("docs/guide.md", "Le tenant zorglub sert oto sous sa marque."),
    (".github/workflows/ci.yml", "  # déployé pour zorglub"),
    ("pyproject.toml", '# contact = "Bruce Wayne"'),
    ("deploy/script.sh", "# la box de wayne-enterprises"),
    ("README.md", "https://app.zorglub.example/org/1"),
])
def test_il_mord_partout_dans_le_depot(rel, contenu, tmp_path):
    """La portée est TOUT le dépôt, pas le code servi : #709 n'avait vu que les
    sources et #747 que les tests, et la réintroduction a touché les deux."""
    occurrences = _scan(tmp_path, {rel: contenu})
    assert len(occurrences) == 1, f"non attrapé dans {rel} : {contenu}"
    assert occurrences[0].path == rel


@pytest.mark.parametrize("ligne", [
    "zorglub",
    "Zorglub",              # la casse ne sauve pas
    "ZORGLUB",
    "zorglub_doc",          # suffixé — la frontière droite est LIBRE
    "zorglub-bridge",
    "zorglub-leads.csv",
    "zorglubabc123",        # concaténé
    "(zorglub)",
    "'zorglub'",
    "app.zorglub.example",
    "  zorglub  ",
])
def test_les_formes_qu_un_nom_prend_en_vrai(ligne, tmp_path):
    assert len(_scan(tmp_path, {"f.py": ligne})) == 1, f"raté : {ligne}"


def test_il_mord_sur_un_nom_de_personne_avec_espace(tmp_path):
    assert len(_scan(tmp_path, {"f.md": "revu par Bruce Wayne le 12/03"})) == 1


def test_un_noqa_CLIENT_sans_raison_ne_sauve_pas(tmp_path):
    """L'échappatoire est NOMINATIVE : sans raison, elle deviendrait le chemin par
    défaut et le garde-fou ne vaudrait plus rien."""
    assert len(_scan(tmp_path, {"f.py": 'X = "zorglub"  # noqa: CLIENT'})) == 1


def test_plusieurs_occurrences_sont_toutes_rendues(tmp_path):
    """Une liste rend de quoi trier : le rapport doit porter chaque site, pas
    seulement le premier."""
    occ = _scan(tmp_path, {"a.py": "zorglub\nautre chose\nBruce Wayne",
                           "b.md": "wayne-enterprises"})
    assert len(occ) == 3
    assert {o.path for o in occ} == {"a.py", "b.md"}
    assert [o.lineno for o in occ if o.path == "a.py"] == [1, 3]


# ── ce qu'il doit LAISSER PASSER ─────────────────────────────────────────────

def test_la_frontiere_gauche_tue_les_faux_positifs_de_sous_chaine(tmp_path):
    """LE cas mesuré le 2026-09-01 : un terme client de quatre lettres ressortait
    3034 fois dans ce dépôt, uniquement à l'intérieur de « registre », « register »
    et « enregistrement ». Sans frontière gauche, le contrôle criait pour tout et
    aurait cessé d'être lu dès le premier jour."""
    texte = ("le registre des connecteurs\n"
             "register_all(mcp)\n"
             "12 459 enregistrements relus\n"
             "REGISTRY = {}\n")
    assert scan_avec(tmp_path, {"f.py": texte}, ["egis"]) == []


def scan_avec(tmp_path, fichiers, termes) -> list:
    return list(scanner(_depot(tmp_path, fichiers), termes))


@pytest.mark.parametrize("ligne", [
    "azorglub",             # collé à gauche : ce n'est pas le mot
    "xzorglub_doc",
    "un tenant tiers",      # la prose générique retenue par #709/#747
    "acme / Jane Doe / app.acme.test",
    "rien à signaler",
])
def test_il_ne_crie_pas_a_tort(ligne, tmp_path):
    assert scan_avec(tmp_path, {"f.py": ligne}, TERMES) == []


@pytest.mark.parametrize("ligne", [
    'X = "zorglub"  # noqa: CLIENT — identifiant fonctionnel, cf. oto-private#85',
    'X = "zorglub"  # noqa: E501, CLIENT — la raison après une autre règle',
    'X = "zorglub"  # noqa: CLIENT: identifiant fonctionnel',
])
def test_la_dette_declaree_passe(ligne, tmp_path):
    assert scan_avec(tmp_path, {"f.py": ligne}, TERMES) == []


def test_un_fichier_non_suivi_par_git_n_est_pas_jugé(tmp_path):
    """Le garde-fou protège ce qui est PUBLIÉ. Un brouillon local non commité ne
    l'est pas — le juger ferait rougir sur du travail en cours."""
    d = _depot(tmp_path, {"suivi.py": "rien"})
    (d / "brouillon.py").write_text("zorglub", encoding="utf-8")
    assert list(scanner(d, TERMES)) == []


def test_les_binaires_sont_ignores_sans_planter(tmp_path):
    d = _depot(tmp_path, {"logo.png": "zorglub"})
    assert list(scanner(d, TERMES)) == []


# ── la mécanique elle-même ───────────────────────────────────────────────────

def test_la_liste_ne_vient_JAMAIS_du_depot(monkeypatch, tmp_path):
    """Le cœur du dispositif : sans source externe, le script ne juge pas — il
    n'a aucun repli sur un fichier versionné. C'est ce qui garantit que la liste
    n'est pas publiée avec le dépôt."""
    monkeypatch.delenv("OTO_NOMS_CLIENTS", raising=False)
    monkeypatch.setenv("OTO_NOMS_CLIENTS_FICHIER", str(tmp_path / "absent.txt"))
    assert charger_termes() is None


def test_la_liste_se_lit_depuis_l_environnement(monkeypatch):
    monkeypatch.setenv("OTO_NOMS_CLIENTS", "zorglub\n# un commentaire\n\nBruce Wayne\n")
    assert charger_termes() == ["zorglub", "Bruce Wayne"]


def test_la_liste_se_lit_depuis_un_fichier(monkeypatch, tmp_path):
    monkeypatch.delenv("OTO_NOMS_CLIENTS", raising=False)
    f = tmp_path / "termes.txt"
    f.write_text("zorglub\nwayne-enterprises\n", encoding="utf-8")
    monkeypatch.setenv("OTO_NOMS_CLIENTS_FICHIER", str(f))
    assert charger_termes() == ["zorglub", "wayne-enterprises"]


def test_le_terme_le_plus_long_gagne(tmp_path):
    """`zorglub` et `zorglub.example` sont tous deux dans la liste : le rapport
    doit nommer le terme le plus précis, sinon il oriente mal la correction."""
    occ = scan_avec(tmp_path, {"f.py": "https://zorglub.example/x"}, TERMES)
    assert [o.terme for o in occ] == ["zorglub.example"]


# ── les trois états du script en ligne de commande ───────────────────────────

def _cli(env: dict[str, str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    e = dict(**{k: v for k, v in __import__("os").environ.items()
                if not k.startswith("OTO_NOMS_CLIENTS")}, **env)
    return subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "lint_noms_clients.py"), str(cwd)],
        capture_output=True, text=True, env=e, cwd=cwd)


def test_sortie_2_quand_il_ne_peut_PAS_juger(tmp_path):
    """Trois états, pas deux — comme `contrat-front.py`. Un contrôle qui ne peut
    pas juger le DIT ; il ne rend jamais un vert muet. C'est l'état d'une PR
    venue d'un fork, qui n'a pas accès aux secrets."""
    d = _depot(tmp_path, {"f.py": "zorglub"})
    r = _cli({"OTO_NOMS_CLIENTS_FICHIER": str(tmp_path / "absent.txt")}, d)
    assert r.returncode == 2
    assert "rien n'a été jugé" in r.stderr


def test_sortie_1_quand_il_trouve(tmp_path):
    d = _depot(tmp_path, {"f.py": "zorglub"})
    r = _cli({"OTO_NOMS_CLIENTS": "zorglub"}, d)
    assert r.returncode == 1
    assert "f.py:1" in r.stderr


def test_sortie_0_quand_le_depot_est_propre(tmp_path):
    d = _depot(tmp_path, {"f.py": "un tenant tiers"})
    r = _cli({"OTO_NOMS_CLIENTS": "zorglub"}, d)
    assert r.returncode == 0, r.stderr


# ── la COUTURE : ce que la CI fait des trois états ───────────────────────────
#
# Le script avait raison depuis le début ; c'est le job qui traduisait « 2 » en
# `exit 0`. Résultat mesuré le 2026-09-01 : le job `noms-clients` a rendu
# « success » sur TOUS ses runs depuis sa pose — dont celui de sa propre fusion —
# alors que le secret n'avait jamais été posé. L'annotation disait la vérité, mais
# `gh pr checks`, la liste des checks et la coche de la PR ne lisent QUE la
# conclusion. Les trois tests ci-dessus prouvaient les morceaux ; aucun ne
# regardait la couture, et le défaut était exactement là.
#
# Ces tests-ci exécutent le VRAI script shell du job, extrait du workflow, avec un
# `lint_noms_clients.py` bouchonné sur chacun des trois codes. On teste ce qui
# tourne, pas une reformulation de ce qu'on croit qu'il fait.

WORKFLOW = RACINE / ".github" / "workflows" / "deploy-canari.yml"


def _script_du_job() -> str:
    """Le `run:` du job `noms-clients`, tel qu'Actions l'exécutera.

    Extrait par un vrai parseur YAML : réécrire l'extraction à la main
    reproduirait les bugs de ce qu'on prétend vérifier.
    """
    import yaml  # dép. dure via oto-core ; présente partout où la suite tourne

    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    steps = [s for s in jobs["noms-clients"]["steps"]
             if "lint_noms_clients.py" in (s.get("run") or "")]
    assert len(steps) == 1, (
        "le job `noms-clients` ne porte plus exactement une étape qui lance "
        "lint_noms_clients.py — ce banc ne vérifie plus rien, le réparer")
    return steps[0]["run"]


def _jouer_le_job(tmp_path: pathlib.Path, code_du_lint: int,
                  depuis_un_fork: str = "") -> subprocess.CompletedProcess:
    """Rejoue le script du job avec un lint bouchonné sur `code_du_lint`."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "lint_noms_clients.py").write_text(
        f"import sys\nsys.exit({code_du_lint})\n", encoding="utf-8")
    script = tmp_path / "job.sh"
    script.write_text(_script_du_job(), encoding="utf-8")
    env = {k: v for k, v in __import__("os").environ.items()
           if not k.startswith(("OTO_NOMS_CLIENTS", "DEPUIS_UN_FORK"))}
    if depuis_un_fork:
        env["DEPUIS_UN_FORK"] = depuis_un_fork
    # `bash -e <fichier>` = le shell par défaut d'Actions sur un runner Linux.
    return subprocess.run(["bash", "-e", str(script)], cwd=tmp_path,
                          capture_output=True, text=True, env=env)


def test_le_job_ROUGIT_quand_il_n_a_PAS_pu_juger(tmp_path):
    """L'état « pas jugé » (code 2) doit ÉCHOUER, jamais réussir avec une note.

    C'est LE défaut corrigé : un garde-fou qui ne peut pas s'exécuter et sort vert
    fabrique une preuve POSITIVE — la forme la plus dangereuse, parce que les
    autres se voient au moins quand on regarde.
    """
    r = _jouer_le_job(tmp_path, 2)
    assert r.returncode != 0, (
        "le job sort vert alors qu'AUCUN jugement n'a été rendu — c'est "
        "exactement la régression du 2026-09-01")
    assert "OTO_NOMS_CLIENTS est absent" in r.stdout


def test_une_PR_de_fork_rougit_SANS_accuser_le_contributeur(tmp_path):
    """Un fork n'a pas accès aux secrets : rien n'est jugé, donc rouge aussi.

    Mais le message doit dire que le code du contributeur n'est pas en cause —
    un garde-fou qui rougit sans expliquer accuse un innocent.
    """
    r = _jouer_le_job(tmp_path, 2, depuis_un_fork="true")
    assert r.returncode != 0
    assert "fork" in r.stdout
    assert "n'est pas en cause" in r.stdout
    assert "Rien ne vous est demandé" in r.stdout


def test_le_job_ROUGIT_quand_il_trouve(tmp_path):
    r = _jouer_le_job(tmp_path, 1)
    assert r.returncode != 0
    assert "dépôt PUBLIC" in r.stdout


def test_le_job_est_vert_seulement_quand_il_a_jugé_et_rien_trouvé(tmp_path):
    r = _jouer_le_job(tmp_path, 0)
    assert r.returncode == 0, r.stdout + r.stderr


def test_le_job_ne_figure_dans_aucun_needs(tmp_path):
    """Rouge, mais il ne retient RIEN : aucun job ne l'attend.

    Le blocage n'achèterait rien — le nom est public dès le push — et déplacerait
    le coût du garde-fou sur toutes les livraisons. Si un `needs` apparaît ici,
    c'est une décision à prendre, pas un détail de câblage.
    """
    import yaml

    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    attendeurs = [nom for nom, j in jobs.items()
                  if "noms-clients" in (j.get("needs") or [])]
    assert attendeurs == [], (
        f"{attendeurs} attend(ent) `noms-clients` : ce job ne doit gater ni un "
        "déploiement ni une livraison")


# ── le VRAI tronc ────────────────────────────────────────────────────────────

def test_le_tronc_reel_si_la_liste_est_disponible():
    """Confronte le dépôt entier à la VRAIE liste — quand elle est là (CI avec le
    secret, ou poste ayant `~/.otomata/noms-clients.txt`).

    Ce test ne peut pas être le seul garde-fou, justement parce qu'il ne juge pas
    partout : la CI lance le script en propre pour distinguer bruyamment « propre »
    de « pas jugé », ce qu'un `skip` de pytest tairait.
    """
    termes = charger_termes()
    if termes is None:
        pytest.skip("liste absente — la CI juge le tronc, cf. lint-noms-clients")
    occurrences = list(scanner(RACINE, termes))
    assert occurrences == [], "\n".join(str(o) for o in occurrences)
