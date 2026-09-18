"""`oto_function`, lot 1 (ADR 0073) : créer, proposer, lire, lister — sur une vraie base.

Ce que ces bancs tiennent :
- une fonction naît avec sa version 1, PROPOSÉE, et rien de publié ;
- une version ne se réécrit pas : proposer ajoute la suivante, et refuse si une autre
  proposition est passée depuis la dernière lecture ;
- une version qui ne pourrait pas s'exécuter est refusée à l'écriture, avec sa raison ;
- le propriétaire est l'org active ou soi, et une fonction ne se voit pas d'ailleurs.
"""
import pytest

from oto_mcp.capabilities import functions as F
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.functions import contract

MOI = "sub-fonction-moi"
ORG = 4242
CTX = ResolvedCtx(sub=MOI, org_id=ORG)

SOURCES = {
    "calcul.py": "def executer(entree):\n    return {'result': entree['a'] * 2}\n",
    "test_calcul.py": "from calcul import executer\n\ndef test_double():\n"
                      "    assert executer({'a': 2})['result'] == 4\n",
}


def _appel(ctx=CTX, **champs):
    return F._dispatch(ctx, F.FunctionInput(**champs))


def _refus(code, **champs):
    with pytest.raises(AuthzDenied) as refus:
        _appel(**champs)
    assert refus.value.code == code, refus.value.message
    return refus.value


def _nouvelle(slug, **champs):
    """Une fonction PROPRE au test : la CI répartit les tests un par un sur plusieurs
    processus, donc aucun test ne peut compter sur ce qu'un autre a écrit."""
    return _appel(**dict({"op": "create", "slug": slug, "title": slug, "sources": SOURCES,
                          "entrypoint": "calcul:executer"}, **champs))


def test_une_fonction_nait_avec_sa_version_1_proposee_et_rien_de_publie(live):
    cree = _nouvelle("naissance", note="première version")
    assert cree["version"] == {"version": 1, "status": "proposee"}
    fiche = cree["function"]
    assert (fiche["owner_type"], fiche["owner_id"]) == ("org", str(ORG))
    assert fiche["published_version"] is None
    assert fiche["latest_version"] == 1


def test_get_rend_le_code_de_la_derniere_version_faute_de_publiee(live):
    _nouvelle("lecture")
    lu = _appel(op="get", slug="lecture")
    assert lu["version"]["version"] == 1
    assert lu["version"]["sources"] == SOURCES
    assert lu["version"]["status"] == "proposee"
    assert lu["version"]["proposed_by"] == MOI


def test_proposer_ajoute_la_suivante_sans_toucher_a_la_precedente(live):
    _nouvelle("suite")
    autre = dict(SOURCES, **{"calcul.py": SOURCES["calcul.py"].replace("* 2", "* 3")})
    rendu = _appel(op="propose", slug="suite", expected_version=1, sources=autre,
                   entrypoint="calcul:executer", note="deuxième")
    assert rendu["version"] == {"version": 2, "status": "proposee"}
    assert _appel(op="get", slug="suite", version=1)["version"]["sources"] == SOURCES
    assert _appel(op="get", slug="suite")["version"]["sources"] == autre
    historique = _appel(op="versions", slug="suite")["versions"]
    assert [v["version"] for v in historique] == [2, 1]
    assert all("sources" not in v for v in historique), "l'historique ne porte pas le code"


def test_proposer_sur_une_lecture_perimee_est_refuse(live):
    _nouvelle("conflit")
    _appel(op="propose", slug="conflit", expected_version=1, sources=SOURCES,
           entrypoint="calcul:executer")
    refus = _refus("version_conflict", op="propose", slug="conflit", expected_version=1,
                   sources=SOURCES, entrypoint="calcul:executer")
    assert refus.status == 409


def test_le_meme_slug_deux_fois_est_refuse(live):
    _nouvelle("doublon")
    _refus("function_exists", op="create", slug="doublon", title="Encore",
           sources=SOURCES, entrypoint="calcul:executer")


@pytest.mark.parametrize("champs, attendu", [
    ({"sources": {"../evasion.py": "x"}}, "à plat"),
    ({"sources": {"calcul.py": "x"}, "entrypoint": "absent:executer"}, "absent des sources"),
    ({"entrypoint": "calcul"}, "module:fonction"),
    ({"requirements": ["requests"]}, "requests"),
    ({"sources": {"gros.py": "x" * (contract.MAX_OCTETS + 1)}, "entrypoint": "gros:f"},
     "octets"),
])
def test_une_version_qui_ne_pourrait_pas_s_executer_est_refusee_a_l_ecriture(live, champs,
                                                                           attendu):
    base = {"op": "create", "slug": "invalide", "title": "Invalide", "sources": SOURCES,
            "entrypoint": "calcul:executer"}
    refus = _refus("invalid_version", **dict(base, **champs))
    assert attendu in refus.message


def test_une_fonction_personnelle_ne_se_voit_pas_depuis_l_org(live):
    _nouvelle("perso", scope="user")
    assert "perso" in [f["slug"] for f in _appel(op="list", scope="user")["functions"]]
    assert "perso" not in [f["slug"] for f in _appel(op="list", scope="org")["functions"]]
    _refus("unknown_function", op="get", slug="perso", scope="org")


def test_une_autre_personne_ne_lit_pas_ma_fonction_personnelle(live):
    _nouvelle("secrete", scope="user")
    autre = ResolvedCtx(sub="sub-fonction-autre", org_id=ORG)
    with pytest.raises(AuthzDenied) as refus:
        _appel(autre, op="get", slug="secrete", scope="user")
    assert refus.value.code == "unknown_function"


def test_sans_org_active_le_refus_nomme_les_deux_issues(live):
    with pytest.raises(AuthzDenied) as refus:
        _appel(ResolvedCtx(sub=MOI, org_id=None), op="get", slug="double")
    assert refus.value.code == "no_active_org" and "scope='user'" in refus.value.message
