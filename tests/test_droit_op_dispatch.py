"""Dispatch `op=` des tools du connecteur `droit` (ADR 0047 §Amendement, appliqué
le 2026-08-11 : 9 tools → 5).

Ce module n'avait AUCUN test : il ne faisait que passer le plat à trois clients FOD
(`fod_ccn`/`fod_loi`/`fod_juris`), et une signature 1-pour-1 se relit à l'œil. La
consolidation par `op=` déplace exactement là le risque : une op mal câblée appelle
silencieusement la mauvaise fonction — et sur ce connecteur, « la mauvaise fonction »
peut être **le mauvais CORPUS** (répondre du KALI à une question de code, ou du texte
d'aujourd'hui à une décision de 1992). Rien ne casse au boot, et la réponse a l'air
juste. D'où, pour chaque op : la fonction FOD appelée, le **mutisme des deux autres
corpus**, le refus explicite d'une op inconnue, et les arguments obligatoires.

S'y ajoutent deux invariants structurels de cette consolidation :
- **elle reste DANS chaque namespace** (`ccn`/`loi`/`juris` sont déclarés séparément
  au registre) — un tool `droit_*` tomberait hors du gate de visibilité, qui résout
  sur le préfixe déclaré ;
- **aucune op n'écrit** : le connecteur est de l'open data en lecture, donc la table
  des ops doit se refermer sur un jeu de fonctions FOD purement lisantes, défauts
  compris.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import McpError

# Importés pour que `monkeypatch.setattr("oto_mcp.fod_*", …)` ait une cible : les
# tools font `from .. import fod_ccn` À L'APPEL, donc c'est l'attribut du PAQUET
# qui est lu (et donc celui qu'on remplace).
import oto_mcp.fod_ccn  # noqa: F401
import oto_mcp.fod_juris  # noqa: F401
import oto_mcp.fod_loi  # noqa: F401


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import droit as D

    m = FastMCP("t")
    D.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _tool_names() -> list[str]:
    from fastmcp import FastMCP
    from oto_mcp.tools import droit as D

    m = FastMCP("t")
    D.register(m)
    return [t.name for t in asyncio.run(m.list_tools())]


@pytest.fixture
def fod(monkeypatch):
    """Les trois clients FOD, remplacés par des mocks — un par corpus, pour qu'un
    appel qui traverse les corpus se voie."""
    mocks = {"ccn": MagicMock(name="fod_ccn"),
             "loi": MagicMock(name="fod_loi"),
             "juris": MagicMock(name="fod_juris")}
    for corpus, mock in mocks.items():
        monkeypatch.setattr(f"oto_mcp.fod_{corpus}", mock)
    return mocks


# La table de correspondance ancienne surface → nouvelle : 9 tools d'avant, 9 ops
# d'après, une fonction FOD chacun. C'est elle qui atteste « zéro capacité perdue ».
# (tool, kwargs d'appel, corpus attendu, fonction FOD attendue, ex-tool)
_OPS = [
    ("ccn_article", {"op": "search", "query": "congés payés"},
     "ccn", "search", "ccn_search"),
    ("ccn_article", {"op": "get", "kali_id": "KALIARTI000012345"},
     "ccn", "article", "ccn_get"),
    ("ccn_conventions", {"idcc": "3090"},
     "ccn", "conventions", "ccn_conventions"),
    ("loi_article", {"op": "get", "code": "CT", "num": "L1242-2"},
     "loi", "article", "loi_article"),
    ("loi_article", {"op": "versions", "code": "CC", "num": "1128"},
     "loi", "versions", "loi_versions"),
    ("loi_article", {"op": "search", "query": "période d'essai CDD"},
     "loi", "search", "loi_search"),
    ("loi_codes", {}, "loi", "codes", "loi_codes"),
    ("juris_decision", {"op": "search", "query": "requalification CDD d'usage"},
     "juris", "search", "juris_search"),
    ("juris_decision", {"op": "get", "decision_id": "JURITEXT000042"},
     "juris", "decision", "juris_get"),
]


@pytest.mark.parametrize("tool,kwargs,corpus,fn,ex_tool", _OPS,
                         ids=[o[4] for o in _OPS])
def test_every_op_routes_to_its_fod_call_and_to_that_corpus_only(
        fod, tool, kwargs, corpus, fn, ex_tool):
    """Chaque op de la nouvelle surface rejoue exactement un ancien tool, et ne
    touche QUE son corpus — répondre du KALI à une question de code passerait
    autrement pour une réponse valide."""
    _tool(tool)(**kwargs)
    getattr(fod[corpus], fn).assert_called_once()
    for other in set(fod) - {corpus}:
        assert not fod[other].mock_calls, (
            f"{ex_tool} → {tool}({kwargs.get('op', '')}) a touché le corpus "
            f"{other}, pas seulement {corpus}")


# --- défauts : une op par défaut est toujours une LECTURE ---------------------

@pytest.mark.parametrize("tool,kwargs,corpus,fn", [
    ("ccn_article", {"query": "prime d'ancienneté"}, "ccn", "search"),
    ("loi_article", {"code": "CT", "num": "L1242-2"}, "loi", "article"),
    ("juris_decision", {"query": "clause de non-concurrence"}, "juris", "search"),
])
def test_default_op_is_the_documented_read(fod, tool, kwargs, corpus, fn):
    """`op` omis = le geste central du corpus (chercher, ou citer un article) —
    jamais autre chose que la lecture annoncée dans la docstring."""
    _tool(tool)(**kwargs)
    getattr(fod[corpus], fn).assert_called_once()


def test_no_op_writes_deletes_or_costs(fod):
    """Connecteur open-data : la surface entière doit se refermer sur un jeu de
    fonctions FOD purement LISANTES. Si une op d'écriture apparaissait un jour
    (le service FOD n'en expose pas aujourd'hui), elle devrait être atteignable
    explicitement — jamais par un défaut — et ce test le rappellerait."""
    lectures = {"ccn": {"search", "article", "conventions"},
                "loi": {"article", "versions", "search", "codes"},
                "juris": {"search", "decision"}}
    for tool, kwargs, corpus, fn, _ in _OPS:
        _tool(tool)(**kwargs)
        assert fn in lectures[corpus]
    appelees = {c: {call[0] for call in m.mock_calls} for c, m in fod.items()}
    assert appelees == lectures, (
        "la surface `droit` a appelé autre chose que ses lectures open-data : "
        f"{appelees}")


# --- refus : op inconnue, arguments obligatoires ------------------------------

@pytest.mark.parametrize("tool,kwargs,attendu", [
    # Arguments COMPLETS : le refus doit tomber sur l'op inconnue, pas sur un
    # argument manquant qui masquerait le vrai motif.
    ("ccn_article", {"query": "x", "kali_id": "k"}, "'search' ou 'get'"),
    ("loi_article", {"query": "x", "code": "CT", "num": "1"},
     "'get', 'versions' ou 'search'"),
    ("juris_decision", {"query": "x", "decision_id": "d"}, "'search' ou 'get'"),
])
def test_unknown_op_is_refused_with_the_allowed_list(fod, tool, kwargs, attendu):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent citerait un texte qu'il n'a pas
    demandé, et le croirait demandé)."""
    with pytest.raises(McpError, match="op doit être") as e:
        _tool(tool)(op="nope", **kwargs)
    assert attendu in str(e.value)
    for m in fod.values():
        assert not m.mock_calls


@pytest.mark.parametrize("tool,kwargs,manquant", [
    # ccn
    ("ccn_article", {"op": "search"}, "query"),
    ("ccn_article", {"op": "get"}, "kali_id"),
    # loi — `code` ET `num` sont requis pour citer/versionner : deviner l'un des
    # deux rendrait le texte d'un AUTRE article, sans erreur.
    ("loi_article", {"op": "get", "num": "L1242-2"}, "code"),
    ("loi_article", {"op": "get", "code": "CT"}, "num"),
    ("loi_article", {"op": "versions", "num": "1128"}, "code"),
    ("loi_article", {"op": "versions", "code": "CC"}, "num"),
    ("loi_article", {"op": "search"}, "query"),
    # juris
    ("juris_decision", {"op": "search"}, "query"),
    ("juris_decision", {"op": "get"}, "decision_id"),
])
def test_missing_required_arg_names_the_op_and_the_arg(fod, tool, kwargs, manquant):
    with pytest.raises(McpError, match=manquant) as e:
        _tool(tool)(**kwargs)
    assert f"op='{kwargs['op']}'" in str(e.value)
    for m in fod.values():
        assert not m.mock_calls


# --- passage des filtres (ce que la fusion aurait pu perdre) ------------------

def test_ccn_search_forwards_all_its_filters(fod):
    """`sort="recent"` et `en_vigueur` portent du savoir métier (les grilles de
    salaire existent en de nombreuses versions périmées, et le dernier avenant
    gagne) : les perdre rendrait une grille obsolète sans le dire."""
    _tool("ccn_article")(op="search", query="grille de salaire", idcc="1285",
                         en_vigueur=False, limit=50, sort="recent")
    args, kwargs = fod["ccn"].search.call_args
    assert args[0] == "grille de salaire"
    assert kwargs == {"idcc": "1285", "en_vigueur": False, "limit": 50,
                      "sort": "recent"}


def test_loi_get_forwards_the_date(fod):
    """La date EST la capacité de `loi` : une décision de 1992 cite la rédaction
    de 1992. Elle passe en 3e argument positionnel de `fod_loi.article`."""
    _tool("loi_article")(op="get", code="CC", num="1128", date="1992-06-30")
    assert fod["loi"].article.call_args.args == ("CC", "1128", "1992-06-30")


def test_loi_search_keeps_code_optional(fod):
    """Chercher DANS TOUS les codes (sans `code`) est une capacité de l'ancien
    `loi_search` : `code` reste optionnel pour op="search", alors qu'il est
    obligatoire pour op="get"/"versions"."""
    _tool("loi_article")(op="search", query="clause de non-concurrence")
    assert fod["loi"].search.call_args.kwargs["code"] is None


def test_juris_search_forwards_all_its_filters(fod):
    """`fond`, `juridiction`, les bornes de date et `expand` (expansion par
    thésaurus juridique, à couper pour du littéral strict) — tous portés par
    l'ancien `juris_search`."""
    _tool("juris_decision")(op="search", query="intermittent", fond="cass",
                            juridiction="cassation", date_min="2015-01-01",
                            date_max="2020-12-31", limit=50, expand=False)
    args, kwargs = fod["juris"].search.call_args
    assert args[0] == "intermittent"
    assert kwargs == {"fond": "cass", "juridiction": "cassation",
                      "date_min": "2015-01-01", "date_max": "2020-12-31",
                      "limit": 50, "expand": False}


# --- ce qui n'a PAS fusionné, et le gate de visibilité ------------------------

@pytest.mark.parametrize("tool", ["ccn_conventions", "loi_codes"])
def test_scope_resolvers_stay_standalone_without_an_op(tool):
    """Les deux résolveurs de périmètre (IDCC, alias de code) rendent un
    CONTENEUR, pas un article, et `ccn_conventions.query` est un substring de
    TITRE (ILIKE) là où `ccn_article.query` est du FTS à stemming français : les
    fusionner surchargerait le même mot de deux sémantiques. Ils n'ont donc pas
    d'`op` — et un `op` qui apparaîtrait ici signalerait la fusion de trop."""
    import inspect
    assert "op" not in inspect.signature(_tool(tool)).parameters


def test_consolidation_stays_inside_each_declared_namespace():
    """Le gate de visibilité résout le namespace sur le PRÉFIXE DÉCLARÉ au
    registre (`juris`/`loi`/`ccn`). Un tool `droit_*` — ou tout autre préfixe —
    serait monté hors de la carte du connecteur : activation, sélection et
    denylist ne le verraient pas."""
    from oto_mcp import providers

    declares = providers.REGISTRY["droit"].namespaces
    noms = _tool_names()
    assert noms, "aucun tool enregistré"
    for nom in noms:
        assert nom.split("_")[0] in declares, (
            f"{nom} sort des namespaces déclarés {declares} — il échapperait au "
            "gate de visibilité du connecteur `droit`")
    # Un namespace vidé par la consolidation serait une capacité perdue.
    assert {n.split("_")[0] for n in noms} == set(declares)
