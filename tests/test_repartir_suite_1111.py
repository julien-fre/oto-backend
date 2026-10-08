"""La répartition de la suite en parts parallèles (#1111) : jamais de trou, jamais de doublon.

Le risque d'un découpage n'est pas d'être mal équilibré, c'est d'être TROUÉ : un fichier
dans aucune part n'est jamais joué, et la CI reste verte. Ces bancs tiennent les trois
garanties de `scripts/repartir_suite.py` — couverture exacte depuis le disque (un fichier
absent des durées tombe quand même dans une part), groupe xdist jamais scindé, verdict qui
refuse un écart — et le câblage dont dépend la garde de mise en production (un job nommé
EXACTEMENT `test`).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "repartir_suite_1111", RACINE / "scripts" / "repartir_suite.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


# ─── La couverture, sur le VRAI arbre ────────────────────────────────────────


@pytest.mark.parametrize("parts", [1, 2, 4, 7])
def test_les_parts_couvrent_exactement_les_fichiers_du_disque(parts):
    attendus = rs.fichiers_de_test(list(rs.CHEMINS_PAR_DEFAUT))
    paniers = rs.decouper("", parts=parts)
    a_plat = [f for p in paniers for f in p]
    assert len(paniers) == parts
    assert all(paniers), "une part vide"
    assert sorted(a_plat) == attendus, "trou ou fichier inventé"
    assert len(a_plat) == len(set(a_plat)), "un fichier dans deux parts"


def test_un_fichier_absent_des_durees_tombe_quand_meme_dans_une_part():
    durees = rs.lire_durees()
    connus = rs.fichiers_de_test(list(rs.CHEMINS_PAR_DEFAUT))
    oublie = connus[len(connus) // 2]
    sans = {f: s for f, s in durees.items() if f != oublie}
    paniers = rs.decouper("", parts=4, durees=sans)
    assert sum(oublie in p for p in paniers) == 1


def test_le_decoupage_est_deterministe():
    """Le plan et chaque part le recalculent séparément : ils doivent tomber d'accord."""
    assert rs.decouper("", parts=4) == rs.decouper("", parts=4)


def test_le_fichier_de_durees_versionne_se_lit_et_ne_vise_que_des_tests():
    durees = rs.lire_durees()
    assert durees
    assert all(f.startswith("tests/") and f.endswith(".py") and s >= 0
               for f, s in durees.items())


def test_la_marge_est_celle_de_l_instrument_d_empreinte():
    """Même règle de dérivation de `-n` que `scripts/empreinte_collecte.py` : un seul
    chiffre, vérifié ici plutôt qu'importé (le plan tourne sans installer le projet)."""
    spec = importlib.util.spec_from_file_location(
        "empreinte_collecte_1111", RACINE / "scripts" / "empreinte_collecte.py")
    ec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ec)
    assert rs.MARGE == ec.MARGE


# ─── Atomes et groupes xdist ─────────────────────────────────────────────────


def _arbre(tmp_path, monkeypatch, fichiers: dict[str, str]):
    for nom, texte in fichiers.items():
        (tmp_path / nom).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / nom).write_text(texte)
    monkeypatch.setattr(rs, "RACINE", tmp_path)


def test_un_xdist_group_ecrit_a_la_main_reunit_ses_fichiers(tmp_path, monkeypatch):
    _arbre(tmp_path, monkeypatch, {
        "tests/test_a.py": 'pytestmark = pytest.mark.xdist_group(name="partage")\n',
        "tests/sous/test_b.py": 'pytestmark = pytest.mark.xdist_group("partage")\n',
        "tests/test_c.py": "def test_c(): pass\n",
        "tests/test_d.py": 'pytestmark = pytest.mark.xdist_group(name="seul")\n',
    })
    a = rs.atomes(rs.fichiers_de_test(["tests"]))
    assert ("tests/sous/test_b.py", "tests/test_a.py") in a
    assert len(a) == 3
    paniers = rs.repartir(a, {"tests/test_c.py": 1.0}, 3)
    porteuses = [p for p in paniers if "tests/test_a.py" in p]
    assert len(porteuses) == 1 and "tests/sous/test_b.py" in porteuses[0]


def test_la_decouverte_suit_les_motifs_de_pytest(tmp_path, monkeypatch):
    _arbre(tmp_path, monkeypatch, {
        "tests/test_a.py": "", "tests/b_test.py": "", "tests/_aide.py": "",
        "tests/conftest.py": "", "tests/.cache/test_x.py": "",
        "tests/__pycache__/test_y.py": "", "tests/donnees/test_z.py": "",
    })
    assert rs.fichiers_de_test(["tests"]) == [
        "tests/b_test.py", "tests/donnees/test_z.py", "tests/test_a.py"]


def test_on_ne_cree_pas_plus_de_parts_que_d_atomes():
    with pytest.raises(rs.Refus, match="vide"):
        rs.repartir([("tests/test_a.py",)], {"tests/test_a.py": 1.0}, 2)


# ─── Répartition par durée ───────────────────────────────────────────────────


def test_le_poids_est_la_duree_pas_le_nombre_de_fichiers():
    durees = {"tests/test_lourd.py": 100.0, **{f"tests/test_{i}.py": 10.0 for i in range(10)}}
    a = [(f,) for f in sorted(durees)]
    paniers = rs.repartir(a, durees, 2)
    lourde = next(p for p in paniers if "tests/test_lourd.py" in p)
    assert lourde == ["tests/test_lourd.py"]


def test_le_nombre_de_parts_suit_la_duree_et_reste_borne():
    a = [(f"tests/test_{i}.py",) for i in range(100)]
    peu = {f: 1.0 for (f,) in a}
    beaucoup = {f: 1000.0 for (f,) in a}
    assert rs.nombre_de_parts(a, peu, coeurs=4) == 1
    assert rs.nombre_de_parts(a, beaucoup, coeurs=4) == rs.PARTS_MAX
    assert rs.nombre_de_parts(a[:2], beaucoup, coeurs=4) == 2


# ─── La cible (sélection d'une autre étape) ──────────────────────────────────


@pytest.mark.parametrize("cible, motif", [
    ("-p evil", "option"),
    ("tests/test_x.py::test_a", "nodeid"),
    ("../ailleurs", "sort du dépôt"),
    ("tests/n_existe_pas.py", "n'existe pas"),
])
def test_une_cible_douteuse_est_refusee(cible, motif):
    with pytest.raises(rs.Refus, match=motif):
        rs.lire_cible(cible)


def test_une_cible_vide_vise_la_suite_et_une_cible_se_repartit_seule():
    assert rs.lire_cible("  ") == list(rs.CHEMINS_PAR_DEFAUT)
    cible = "tests/test_repartir_suite_1111.py tests/test_groupes_xdist_963.py"
    paniers = rs.decouper(cible, parts=2)
    assert sorted(f for p in paniers for f in p) == sorted(cible.split())


# ─── Le verdict de l'agrégateur ──────────────────────────────────────────────


def test_le_verdict_accepte_une_couverture_exacte():
    assert rs.comparer(["a::1", "a::2", "b::1"], {"p0": ["a::1", "a::2"], "p1": ["b::1"]}) == []


def test_le_verdict_nomme_le_trou_le_doublon_et_l_intrus():
    ecarts = rs.comparer(["a::1", "a::2", "b::1"],
                         {"p0": ["a::1", "b::1"], "p1": ["b::1", "c::9"]})
    texte = "\n".join(ecarts)
    assert "TROU" in texte and "a::2" in texte
    assert "DOUBLON" in texte and "b::1" in texte
    assert "c::9" in texte
    assert "SOMME" not in texte or "4 collectés" in texte


def test_le_verdict_rougit_quand_une_part_n_a_rien_publie(tmp_path):
    (tmp_path / "ref").mkdir()
    (tmp_path / "ref" / "reference.txt").write_text("a::1\nb::1\n")
    (tmp_path / "suite-part-0").mkdir()
    (tmp_path / "suite-part-0" / "collecte.txt").write_text("a::1\n")
    code = rs.main(["verifier", "--dossier", str(tmp_path), "--reference",
                    "ref/reference.txt", "--parts", "2"])
    assert code == 1
    (tmp_path / "suite-part-1").mkdir()
    (tmp_path / "suite-part-1" / "collecte.txt").write_text("b::1\n")
    assert rs.main(["verifier", "--dossier", str(tmp_path), "--reference",
                    "ref/reference.txt", "--parts", "2"]) == 0


def test_les_nodeids_du_resume_des_avertissements_ne_comptent_pas():
    sortie = ("tests/test_a.py::test_1\ntests/test_a.py::test_2[x y]\n\n"
              "=============== warnings summary ===============\n"
              "tests/test_a.py::test_1\n  /chemin: DeprecationWarning\n"
              "2 tests collected in 0.1s\n")
    assert rs.lire_nodeids(sortie) == ["tests/test_a.py::test_1", "tests/test_a.py::test_2[x y]"]


# ─── Le nombre de workers, dérivé de la machine ──────────────────────────────


def test_les_workers_se_derivent_de_la_memoire_puis_des_coeurs():
    go = 1024**3
    assert rs.workers_pour(16 * go, int(0.4 * go), coeurs=4)[0] == 4      # borné par les cœurs
    assert rs.workers_pour(2 * go, int(0.4 * go), coeurs=4)[0] == 3       # 1,6 Go / 0,4 = 4 − 1
    assert rs.workers_pour(int(0.5 * go), int(0.4 * go), coeurs=4)[0] == 1
    with pytest.raises(rs.Refus):
        rs.workers_pour(16 * go, 0, coeurs=4)


# ─── Les durées, depuis les rapports JUnit ───────────────────────────────────


def test_les_durees_se_relisent_dans_le_junit_de_pytest(tmp_path):
    connus = {"tests/test_a.py", "tests/sous/test_b.py"}
    (tmp_path / "j.xml").write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.test_a" name="test_1" time="1.5"/>'
        '<testcase classname="tests.test_a.TestK" name="test_2[p]@tests/test_a.py" time="2"/>'
        '<testcase classname="tests.sous.test_b" name="test_3" time="0.25"/>'
        '<testcase classname="" name="tests.sous.test_b" time="0.25"/>'
        '<testcase classname="ailleurs.test_z" name="test_9" time="9"/>'
        '</testsuite></testsuites>')
    durees, orphelins = rs.durees_depuis_junit([tmp_path / "j.xml"], connus)
    assert durees == {"tests/test_a.py": 3.5, "tests/sous/test_b.py": 0.5}
    assert orphelins == 1
    rs.ecrire_durees(durees, "banc", tmp_path / "d.json")
    assert json.loads((tmp_path / "d.json").read_text())["durees"]["tests/test_a.py"] == 3.5


# ─── Le câblage CI dont dépend la garde de mise en production ────────────────


def _workflow(nom: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((RACINE / ".github" / "workflows" / nom).read_text())


def _job_test_aggrege(jobs: dict) -> None:
    test = jobs["test"]
    assert test.get("name", "test") == "test"
    besoins = test["needs"] if isinstance(test["needs"], list) else [test["needs"]]
    assert "suite" in besoins
    assert "always()" in str(test["if"])
    premier = test["steps"][0]
    assert "needs.suite.result" in json.dumps(premier) and "exit 1" in premier["run"]


def test_le_job_test_de_preproduction_porte_le_verdict_de_la_suite():
    """`scripts/garde_preprod_verte.py` et le script de bascule lisent un job nommé
    EXACTEMENT `test` ; un job appelé par `uses:` s'affiche « suite / … ». Le job `test`
    doit donc exister, attendre la suite, tourner même quand elle rougit, et ne conclure
    `success` que si elle a conclu `success`."""
    jobs = _workflow("deploy-canari.yml")["jobs"]
    assert jobs["suite"]["uses"] == "./.github/workflows/suite-tests.yml"
    assert jobs["suite"]["needs"] == ["selection"]
    assert jobs["suite"]["with"]["cible"] == "${{ needs.selection.outputs.cible }}"
    _job_test_aggrege(jobs)
    assert "test" in jobs["deploy-preprod"]["needs"]


def test_au_tag_la_suite_complete_tourne_sur_la_ref_validee_et_deploy_l_attend():
    jobs = _workflow("deploy.yml")["jobs"]
    suite = jobs["suite"]
    assert suite["uses"] == "./.github/workflows/suite-tests.yml"
    assert "cible" not in suite.get("with", {}), "au tag : la suite COMPLÈTE"
    assert suite["with"]["ref"] == "${{ needs.tag.outputs.ref }}"
    assert "tag invalide" in jobs["tag"]["steps"][0]["run"]
    _job_test_aggrege(jobs)
    constat = [s for s in jobs["test"]["steps"] if "--constat" in s.get("run", "")]
    assert constat and constat[0]["continue-on-error"] is True
    assert "junit-suite" in constat[0]["run"]
    assert "test" in jobs["deploy"]["needs"]


def test_suite_tests_est_appelable_et_son_verdict_attend_tout():
    wf = _workflow("suite-tests.yml")
    declencheurs = wf.get("on", wf.get(True))
    assert {"cible", "ref"} <= set(declencheurs["workflow_call"]["inputs"])
    verdict = wf["jobs"]["verdict"]
    assert set(verdict["needs"]) == {"plan", "part", "reference"}
    assert "always()" in str(verdict["if"])
    assert "fromJSON(needs.plan.outputs.parts)" in wf["jobs"]["part"]["strategy"]["matrix"]["part"]
    publies = [s["with"]["name"] for s in verdict["steps"]
               if "upload-artifact" in s.get("uses", "")]
    assert "junit-suite" in publies
    fusion = next(s for s in verdict["steps"] if "fusionner" in s.get("run", ""))
    assert "always()" in fusion["if"], "le rapport fusionné doit exister SURTOUT quand une part rougit"


def test_la_fusion_junit_reunit_les_parts_et_refuse_un_rapport_absent(tmp_path):
    for i in range(2):
        (tmp_path / f"j{i}.xml").write_text(
            f'<testsuites><testsuite name="pytest"><testcase classname="tests.test_{i}" '
            f'name="test_a" time="1"/></testsuite></testsuites>')
    sortie = tmp_path / "f.xml"
    assert rs.fusionner_junit([tmp_path / "j0.xml", tmp_path / "j1.xml"], sortie) == 2
    with pytest.raises(rs.Refus, match="absent"):
        rs.fusionner_junit([tmp_path / "j0.xml", tmp_path / "manque.xml"], sortie)
