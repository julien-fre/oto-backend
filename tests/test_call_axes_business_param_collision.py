"""Les jetons de contexte ne doivent JAMAIS marcher sur les arguments métier (issue #250).

Les six jetons d'appel (ADR 0038) ont longtemps porté les noms NUS `account`, `org`,
`group`, `project`, `instance`, `run_id` — dans le même espace plat que les arguments des
tools, alors que ce sont précisément les mots d'une API B2B. Deux collisions vécues en
PROD :

- 2026-07-04 — `oto_use_org(org=<cible>)` : l'org CIBLE mangée par le retrait de l'axe
  (`UseOrgInput.org Field required`).
- 2026-07-28 — `aiark_company_search(account=…)` : `account` est le filtre firmographique
  d'AI Ark. `oto_call` le retirait → requête sans filtre → AI Ark répondait 200 avec sa
  base entière (72M sociétés). Aucune erreur, un résultat faux, et le connecteur n'étant
  dans le socle de personne (ADR 0050) tous les appels passaient par `oto_call` : le
  filtre n'a jamais fonctionné.

Les jetons sont désormais préfixés `_`. Ces tests verrouillent les deux moitiés du
contrat : le préfixe est bien porté partout, et un argument métier homonyme survit.
"""
import ast
import pathlib

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import call_axes

AXIS_NAMES = {a.param for a in call_axes.AXES}
TOOLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "tools"


def _schema(*props: str) -> dict:
    return {"type": "object", "properties": {p: {} for p in props}}


# ── Le contrat de nommage ────────────────────────────────────────────────────

def test_every_axis_is_namespaced():
    """Aucun jeton ne doit reprendre un nom nu : c'est ce qui a causé les deux incidents."""
    assert AXIS_NAMES == {"_account", "_project", "_run_id", "_org", "_group", "_instance"}
    for name in AXIS_NAMES:
        assert name.startswith("_"), name


def test_legacy_renames_are_derived_not_hardcoded():
    """La table ancien→nouveau se dérive des axes (rien à tenir à jour à la main)."""
    assert call_axes.LEGACY_PARAM_RENAMES == {n.lstrip("_"): n for n in AXIS_NAMES}


# ── Le balayage ne touche que les jetons ─────────────────────────────────────

def test_sweep_removes_namespaced_tokens():
    args = {name: "x" for name in AXIS_NAMES} | {"query": "acme"}
    call_axes.strip_unconsumed_axes(args)
    assert args == {"query": "acme"}


def test_sweep_leaves_business_arguments_alone():
    """Le cas aiark : `account` nu est un argument métier, il traverse intact."""
    args = {"account": {"name": {"any": {"include": ["Amazon"]}}}, "size": 10}
    call_axes.strip_unconsumed_axes(args)
    assert args == {"account": {"name": {"any": {"include": ["Amazon"]}}}, "size": 10}


# ── L'ancien nom ne disparaît pas en silence ─────────────────────────────────

def test_legacy_bare_token_raises_with_the_new_name():
    """Pas d'alias, mais pas de retrait muet non plus : tourner sous une AUTRE org que
    celle demandée est pire que se faire rejeter."""
    with pytest.raises(McpError) as e:
        call_axes.reject_legacy_axis_names({"org": 3, "query": "acme"}, _schema("query"))
    assert "_org" in str(e.value)


def test_legacy_check_ignores_a_declared_business_param():
    """`account` sur aiark EST un argument de la cible → aucun refus."""
    call_axes.reject_legacy_axis_names(
        {"account": {"domain": {"any": {"include": ["amazon.com"]}}}},
        _schema("account", "contact", "page", "size"))


# ── Tripwire dérivé des sources ──────────────────────────────────────────────

def _tools_declaring(names: set) -> list:
    """(module, tool, params) pour chaque tool des sources dont un paramètre est dans
    `names`. Dérivé de l'AST : un connecteur ajouté demain est couvert d'office."""
    found = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any("tool" in ast.dump(d) for d in node.decorator_list):
                continue
            params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
            hit = params & names
            if hit:
                found.append((path.stem, node.name, hit))
    return found


# `oto_call` EST le dispatcher : son `_org` top-level est le jeton lui-même, offert en
# raccourci pour ne pas avoir à l'enfouir dans `arguments`. Seule exception légitime.
_TOKEN_OWNERS = {("meta", "oto_call")}


def test_no_tool_declares_a_namespaced_token_as_its_own_param():
    """TRIPWIRE — le préfixe `_` est réservé à la plateforme. Un tool qui s'approprierait
    `_org`/`_account` se le ferait manger par le balayage, exactement comme aiark avant."""
    stolen = [(m, t, p) for m, t, p in _tools_declaring(AXIS_NAMES)
              if (m, t) not in _TOKEN_OWNERS]
    assert not stolen, f"jetons réservés déclarés en argument métier : {stolen}"


def test_business_params_named_like_legacy_tokens_survive():
    """TRIPWIRE — les tools qui portent un `account`/`org`/… métier (aiark, pennylane,
    google…) traversent le balayage intacts. Dérivé des sources."""
    legacy = set(call_axes.LEGACY_PARAM_RENAMES)
    collisions = _tools_declaring(legacy)
    assert collisions, "scan AST muet — le détecteur ne voit plus les tools"
    for module, tool, params in collisions:
        args = {name: "valeur-métier" for name in params}
        call_axes.strip_unconsumed_axes(args)
        assert args == {name: "valeur-métier" for name in params}, f"{module}.{tool}"


@pytest.mark.parametrize("tool_name", ["aiark_company_search", "aiark_people_search"])
def test_aiark_account_is_a_business_filter(tool_name):
    """Régression nommée : l'axe compte ne s'applique pas à aiark (ni multi-credential ni
    porteur d'identités) et son `account` métier n'est plus dans l'espace des jetons."""
    assert "_account" not in {a.param for a in call_axes.axes_for(tool_name)}
    args = {"account": {"domain": {"any": {"include": ["amazon.com"]}}}, "size": 1}
    call_axes.strip_unconsumed_axes(args)
    assert args["account"] == {"domain": {"any": {"include": ["amazon.com"]}}}
