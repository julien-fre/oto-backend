"""La sélection au push (#1185) ne laisse aucun trou silencieux.

Au push sur `main`, la CI ne joue que les volets touchés et le socle ; la suite complète
attend le tag. Ce qui rend cette économie acceptable, c'est que la table « fichier →
volet » soit COMPLÈTE et VIVANTE : tout fichier d'`oto_mcp/` y est rangé (sinon la suite
complète part, et ce test rougit pour qu'on le range), aucune entrée ne vise un fichier
disparu, le socle existe. Et le script, devant ce qu'il ne sait pas juger (base inconnue,
fichier hors volet), joue tout plutôt que rien.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

_RACINE = pathlib.Path(__file__).resolve().parents[1]
_CHEMIN = _RACINE / "scripts" / "selection_tests.py"
_spec = importlib.util.spec_from_file_location("selection_tests", _CHEMIN)
sel = importlib.util.module_from_spec(_spec)
sys.modules["selection_tests"] = sel  # dataclasses résout ses annotations par le module
_spec.loader.exec_module(sel)


@pytest.fixture(scope="module")
def table():
    return sel.charger_table(sel.TABLE, _RACINE)


@pytest.fixture(scope="module")
def index():
    return sel.Index(_RACINE, sel.fichiers_de_tests(_RACINE))


def _sources():
    return sel._fichiers(_RACINE, "oto_mcp")


# ── La table ───────────────────────────────────────────────────────────────────

def test_tout_fichier_d_oto_mcp_est_range(table):
    orphelins = [
        f for f in _sources()
        if not table.force_complete(f) and not any(v.couvre(f) for v in table.volets)
    ]
    assert not orphelins, (
        "Fichiers d'oto_mcp/ rangés dans aucun volet de tests/volets.toml — chaque push qui les "
        "touche jouera la suite complète. Les ranger dans le volet de leur domaine, ou dans "
        "[complete] s'ils sont transverses :\n" + "\n".join(orphelins)
    )


def test_un_fichier_force_la_suite_ou_appartient_a_un_volet_jamais_les_deux(table):
    doubles = [f for f in _sources() if table.force_complete(f) and any(v.couvre(f) for v in table.volets)]
    assert not doubles, "rangés à la fois dans [complete] et dans un volet :\n" + "\n".join(doubles)


_HORS_DEPOT = {".git", ".venv", "__pycache__", "oto_mcp.egg-info", "node_modules"}


def _tous_les_fichiers():
    return [
        p.relative_to(_RACINE).as_posix() for p in _RACINE.rglob("*")
        if p.is_file() and not (_HORS_DEPOT & set(p.relative_to(_RACINE).parts))
    ]


def test_aucune_entree_morte():
    """Une entrée qui ne vise plus rien ment sur ce qu'elle protège. Pour la famille, chaque
    gabarit doit viser au moins un connecteur (tous n'ont pas toutes les formes)."""
    import tomllib

    brut = tomllib.loads(sel.TABLE.read_text(encoding="utf-8"))
    fichiers = _tous_les_fichiers()
    tests = sel.fichiers_de_tests(_RACINE)
    morts = []

    def vivant(motif, parmi):
        rx = sel.glob_en_regex(motif)
        return any(rx.match(f) for f in parmi)

    for motif in brut["complete"]["chemins"]:
        if not vivant(motif, fichiers):
            morts.append("[complete] " + motif)
    for v in brut["volet"]:
        morts += ["volet {} : source {}".format(v["nom"], m) for m in v["sources"] if not vivant(m, fichiers)]
        morts += ["volet {} : tests {}".format(v["nom"], m) for m in v["tests"] if not vivant(m, tests)]
    table = sel.charger_table(sel.TABLE, _RACINE)
    for f in brut["famille"]:
        instances = [v for v in table.volets if v.nom.startswith(f["nom"] + " ")]
        assert instances, "la famille {} ne trouve aucun nom".format(f["nom"])
        for gabarit in f["sources"]:
            if not any(vivant(gabarit.replace("{nom}", v.nom.split(" ", 1)[1]), fichiers) for v in instances):
                morts.append("famille {} : source {}".format(f["nom"], gabarit))
        for gabarit in f["tests"]:
            if not any(vivant(gabarit.replace("{nom}", v.nom.split(" ", 1)[1]), tests) for v in instances):
                morts.append("famille {} : tests {}".format(f["nom"], gabarit))
    assert not morts, "entrées qui ne visent plus rien :\n" + "\n".join(morts)


def test_le_socle_existe_et_se_joue_lui_meme(table):
    absents = [t for t in table.socle if not (_RACINE / t).is_file()]
    assert not absents, "fichiers du socle introuvables :\n" + "\n".join(absents)
    assert "tests/test_selection_tests.py" in table.socle


def test_globs():
    rx = sel.glob_en_regex
    assert rx("oto_mcp/*.py").match("oto_mcp/server.py")
    assert not rx("oto_mcp/*.py").match("oto_mcp/db/users.py")
    assert rx("tests/**/test_x.py").match("tests/test_x.py")
    assert rx("tests/**/test_x.py").match("tests/datastore/test_x.py")
    assert rx("oto_mcp/access/**").match("oto_mcp/access/sous/x.py")


# ── La sélection ───────────────────────────────────────────────────────────────

def test_un_fichier_hors_volet_joue_la_suite_complete(table, index):
    s = sel.selectionner(["oto_mcp/module_que_la_table_ignore.py"], table, _RACINE, index)
    assert s.complete and s.cible == ""
    assert "oto_mcp/module_que_la_table_ignore.py" in s.raisons[0]


def test_un_fichier_transverse_joue_la_suite_complete(table, index):
    s = sel.selectionner(["oto_mcp/server.py"], table, _RACINE, index)
    assert s.complete and s.cible == ""


def test_un_connecteur_joue_ses_tests_et_le_socle(table, index):
    s = sel.selectionner(["oto_mcp/tools/wttj.py"], table, _RACINE, index)
    assert not s.complete
    assert "tests/test_wttj_tools.py" in s.tests
    assert set(table.socle) <= set(s.tests)
    assert s.cible == " ".join(s.tests)


def test_un_module_joue_ses_importeurs_directs(table, index):
    importeurs = index.importeurs({"oto_mcp.transcription_worker"})
    assert importeurs
    s = sel.selectionner(["oto_mcp/transcription_worker.py"], table, _RACINE, index)
    assert importeurs <= set(s.tests)


def test_un_test_modifie_est_joue(table, index):
    s = sel.selectionner(["tests/test_wttj_tools.py"], table, _RACINE, index)
    assert not s.complete
    assert "tests/test_wttj_tools.py" in s.tests


def test_un_script_joue_les_tests_qui_le_citent(table, index):
    s = sel.selectionner(["scripts/garde_preprod_verte.py"], table, _RACINE, index)
    assert not s.complete
    assert "tests/test_garde_preprod_verte.py" in s.tests


def test_au_dela_du_seuil_la_suite_est_complete(index):
    table = sel.charger_table(sel.TABLE, _RACINE)
    table.seuil_part = 0.001
    s = sel.selectionner(["oto_mcp/tools/wttj.py"], table, _RACINE, index)
    assert s.complete and "seuil_part" in s.raisons[0]


# ── Le diff ────────────────────────────────────────────────────────────────────

def test_une_base_nulle_joue_la_suite_complete():
    assert isinstance(sel.fichiers_modifies("0" * 40, "HEAD"), str)
    assert isinstance(sel.fichiers_modifies("", "HEAD"), str)


def _depot(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tmp_path, check=True, capture_output=True, text=True,
        ).stdout.strip()
    git("init", "-q")
    return git


def test_une_base_absente_de_l_historique_joue_la_suite_complete(tmp_path):
    git = _depot(tmp_path)
    (tmp_path / "a.py").write_text("a")
    git("add", "-A")
    git("commit", "-qm", "un")
    raison = sel.fichiers_modifies("1234567" * 5 + "89abc", "HEAD", tmp_path)
    assert isinstance(raison, str) and "historique" in raison


def test_un_push_de_plusieurs_commits_voit_tous_leurs_fichiers(tmp_path):
    """La base est `github.event.before`, pas le parent : sinon seuls les fichiers du
    DERNIER commit d'un push seraient jugés."""
    git = _depot(tmp_path)
    (tmp_path / "zero.py").write_text("0")
    git("add", "-A")
    git("commit", "-qm", "avant le push")
    avant = git("rev-parse", "HEAD")
    for nom in ("premier.py", "second.py"):
        (tmp_path / nom).write_text(nom)
        git("add", "-A")
        git("commit", "-qm", nom)
    assert sorted(sel.fichiers_modifies(avant, "HEAD", tmp_path)) == ["premier.py", "second.py"]


def test_un_diff_illisible_fait_echouer_au_lieu_de_tout_sauter(tmp_path):
    git = _depot(tmp_path)
    (tmp_path / "a.py").write_text("a")
    git("add", "-A")
    git("commit", "-qm", "un")
    base = git("rev-parse", "HEAD")
    with pytest.raises(SystemExit):
        sel.fichiers_modifies(base, "reference-qui-n-existe-pas", tmp_path)


# ── Le constat au tag ──────────────────────────────────────────────────────────

def test_le_constat_dit_quel_rouge_aurait_file(tmp_path, table, index):
    s = sel.selectionner(["oto_mcp/tools/wttj.py"], table, _RACINE, index)
    hors = next(t for t in sel.fichiers_de_tests(_RACINE) if t.startswith("tests/datastore/") and t not in s.tests)
    racine = ET.Element("testsuites")
    suite = ET.SubElement(racine, "testsuite")
    vu = ET.SubElement(suite, "testcase", classname="tests.test_wttj_tools", name="test_a")
    ET.SubElement(vu, "failure")
    file = ET.SubElement(suite, "testcase", classname=hors[:-3].replace("/", ".") + ".TestX", name="test_b")
    ET.SubElement(file, "error")
    ET.SubElement(suite, "testcase", classname="tests.test_openapi", name="test_vert")
    junit = tmp_path / "junit.xml"
    ET.ElementTree(racine).write(junit)
    texte = sel.constat(junit, s, _RACINE)
    assert "`tests/test_wttj_tools.py` : vu au push" in texte
    assert "`{}` : **AURAIT FILÉ**".format(hors) in texte
    assert "test_openapi" not in texte
