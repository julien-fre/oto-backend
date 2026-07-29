"""Un ARGUMENT MÉTIER qui porte le nom d'un axe d'appel ne doit jamais être avalé.

`oto_call` s'exécute hors middleware : il rejoue lui-même la pose des axes-contexte
(`org`/`project`/`group`/`instance`/`run_id`/`account`) puis balaie ceux qui restent, sinon
la cible — qui ne les déclare pas — échouerait à la validation. Ce balayage était
INCONDITIONNEL : tout argument portant un nom d'axe disparaissait, y compris quand c'était
un vrai paramètre de la cible.

Vécu le 2026-07-28 : `aiark_company_search(account=…)` — où `account` est le filtre
firmographique d'AI Ark, pas un choix de compte de connecteur — perdait son filtre en
silence. AI Ark répondait 200 avec la base entière (72M sociétés) : aucune erreur nulle
part, un résultat faux, et le connecteur n'étant dans le socle de personne (ADR 0050),
100% des appels passaient par `oto_call` → le filtre n'a jamais fonctionné.

Le tripwire du bas est AUTO-MAINTENU : il dérive les collisions des sources `tools/*.py`.
Un nouveau connecteur avec un paramètre `org`/`account`/… est couvert sans rien y toucher.
"""
import ast
import pathlib

import pytest

from oto_mcp import call_axes

AXIS_NAMES = {a.param for a in call_axes.AXES}
TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "tools"


def _schema(*props: str) -> dict:
    return {"type": "object", "properties": {p: {} for p in props}}


def test_axis_name_declared_by_the_target_survives():
    """Le cas aiark : `account` est un argument métier → il doit rester intact."""
    args = {"account": {"name": {"any": {"include": ["Amazon"]}}}, "size": 10}
    call_axes.strip_unconsumed_axes(args, _schema("account", "contact", "page", "size"))
    assert args == {"account": {"name": {"any": {"include": ["Amazon"]}}}, "size": 10}


def test_axis_name_not_declared_is_stripped():
    """Le cas d'origine : un jeton de contexte sans effet ne doit pas casser la cible."""
    args = {"instance": "member:2:x:folk", "org": 3, "query": "acme"}
    call_axes.strip_unconsumed_axes(args, _schema("query"))
    assert args == {"query": "acme"}


def test_every_axis_name_is_covered():
    """Aucun axe n'échappe au balayage quand la cible ne le déclare pas."""
    args = {name: "x" for name in AXIS_NAMES} | {"query": "acme"}
    call_axes.strip_unconsumed_axes(args, _schema("query"))
    assert args == {"query": "acme"}


def test_no_schema_strips_everything():
    """Cible sans schéma exploitable → on reste sur l'ancien comportement (prudent)."""
    args = {"org": 3, "query": "acme"}
    call_axes.strip_unconsumed_axes(args, None)
    assert args == {"query": "acme"}


def _tools_declaring_axis_names() -> list[tuple[str, str, set[str]]]:
    """(module, tool, params en collision) pour chaque tool des sources `tools/*.py`."""
    found = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any("tool" in ast.dump(d) for d in node.decorator_list):
                continue
            params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
            clash = params & AXIS_NAMES
            if clash:
                found.append((path.stem, node.name, clash))
    return found


def test_declared_business_params_survive_the_sweep():
    """TRIPWIRE — pour CHAQUE tool déclarant un paramètre qui porte un nom d'axe, cet
    argument doit survivre au balayage d'`oto_call`. Dérivé des sources : un connecteur
    ajouté demain avec un champ `account`/`org`/`group` est couvert d'office."""
    collisions = _tools_declaring_axis_names()
    assert collisions, "scan AST muet — le détecteur de collisions ne voit plus les tools"
    for module, tool, clash in collisions:
        args = {name: "valeur-métier" for name in clash}
        call_axes.strip_unconsumed_axes(args, _schema(*clash))
        assert args == {name: "valeur-métier" for name in clash}, (
            f"{module}.{tool} perd {sorted(clash - set(args))} en passant par oto_call")


@pytest.mark.parametrize("tool_name", ["aiark_company_search", "aiark_people_search"])
def test_aiark_account_is_a_business_filter_not_an_axis(tool_name):
    """Régression nommée : l'axe `account` ne s'applique PAS à aiark (le connecteur n'est
    ni multi-credential ni porteur d'identités) — donc rien ne le consomme en amont, et
    seul le garde du balayage empêche la perte du filtre."""
    assert "account" not in {a.param for a in call_axes.axes_for(tool_name)}
    args = {"account": {"domain": {"any": {"include": ["amazon.com"]}}}}
    call_axes.strip_unconsumed_axes(args, _schema("account", "contact", "page", "size"))
    assert args["account"] == {"domain": {"any": {"include": ["amazon.com"]}}}
