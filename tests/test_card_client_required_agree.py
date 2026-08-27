"""Garde-fou carte↔client : un champ déclaré FACULTATIF sur la carte ne doit pas
être exigé par le client oto-core.

Deux repos, deux déclarations du même fait — « ce champ est-il nécessaire ? » :

- la **carte** (`providers/<nom>.py`) est le contrat avec l'UTILISATEUR : elle pilote le
  formulaire dashboard, la validation REST et le packing au coffre
  (`CredentialField.required`) ;
- le **client** (oto-core) est le contrat avec l'API : `self.x = x or require_secret(…)`
  = obligatoire (il LÈVE si absent), `x or get_secret(…, None)` = facultatif.

Rien ne relie structurellement les deux. Le sens dangereux est **carte plus laxiste
que le client** : la pose réussit (le champ est facultatif au formulaire), puis le
client lève à la PREMIÈRE utilisation — un connecteur qui se configure et ne marche
pas. Cette sonde le ferme statiquement.

Le sens inverse (carte plus stricte que le client) n'est PAS dérivable du code :
« l'API n'a pas besoin de ce champ » est une connaissance de l'API, pas du
programme. Il se couvre par un test explicite par connecteur — cf.
`test_zohodesk_card.py` (l'`org_id` requis rendait le connecteur imposable, 28/07).
"""
from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path

import pytest

from oto_mcp import providers

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "oto_mcp" / "tools"


def _client_class(tool_module: str):
    """Classe cliente oto-core d'un module d'outil, via la convention
    `def _client() -> <Classe>` + son import (même résolution que
    `test_tools_client_methods_exist.py`). None si hors convention."""
    path = _TOOLS_DIR / f"{tool_module}.py"
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(), filename=str(path))
    clsname = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_client":
            if isinstance(node.returns, ast.Name):
                clsname = node.returns.id
            break
    if not clsname:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if any(a.name == clsname for a in node.names):
                try:
                    return getattr(importlib.import_module(node.module), clsname)
                except Exception:  # noqa: BLE001 — extra non installé
                    return None
    return None


def _hard_required_from_source(src: str) -> set[str]:
    """Paramètres EXIGÉS dans un source d'`__init__` : motif
    `self.<attr> = <param> or require_secret(...)` (pas de repli sur None).
    ⚠️ `textwrap.dedent`, PAS `inspect.cleandoc` (fait pour les docstrings : il
    mange l'indentation du corps → IndentationError)."""
    tree = ast.parse(textwrap.dedent(src))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.BoolOp):
            continue
        if not isinstance(node.value.op, ast.Or):
            continue
        vals = node.value.values
        if len(vals) != 2 or not isinstance(vals[0], ast.Name):
            continue
        fallback = vals[1]
        if (isinstance(fallback, ast.Call) and isinstance(fallback.func, ast.Name)
                and fallback.func.id == "require_secret"):
            out.add(vals[0].id)
    return out


def _hard_required_params(cls) -> set[str]:
    """Idem, pour une classe cliente réelle."""
    try:
        return _hard_required_from_source(inspect.getsource(cls.__init__))
    except (OSError, TypeError, SyntaxError):  # pragma: no cover
        return set()


def _cases():
    """(connecteur, module, champs facultatifs de la carte)."""
    out = []
    for c in providers.REGISTRY.values():
        optional = {f.name for f in c.secret_fields if not f.required}
        if not optional:
            continue
        for mod in (c.modules or (c.name,)):
            out.append((c.name, mod, optional))
    return out


_CASES = _cases()


def test_probe_actually_covers_something():
    """Anti-couverture-fantôme : une sonde qui ne teste plus rien passe en silence.
    `zohodesk` est le cas d'école (28/07) — s'il sort de la couverture, c'est que la
    convention `_client() -> Classe` a bougé et que la sonde s'est vidée."""
    assert _CASES, "aucun connecteur avec champ facultatif — sonde inerte ?"
    assert any(c == "zohodesk" for c, _, _ in _CASES), (
        "zohodesk doit rester couvert (cas d'école du garde-fou carte↔client)")


@pytest.mark.parametrize("connector, tool_module, optional", _CASES,
                         ids=[f"{c}:{m}" for c, m, _ in _CASES])
def test_optional_card_field_is_not_required_by_client(connector, tool_module, optional):
    cls = _client_class(tool_module)
    if cls is None:
        pytest.skip(f"{tool_module}: pas de `_client() -> Classe` résoluble")
    hard = _hard_required_params(cls)
    clash = sorted(optional & hard)
    assert not clash, (
        f"carte `{connector}` déclare {clash} FACULTATIF(S), mais {cls.__name__} "
        f"les exige (`require_secret`) → la pose réussira et le connecteur lèvera "
        f"au premier appel. Rendre le champ optionnel côté client "
        f"(`get_secret(..., None)`) ou requis sur la carte (`required=True`).")


def test_detector_recognises_both_forms():
    """La sonde distingue bien requis (`require_secret`) et facultatif
    (`get_secret(..., None)`) — sinon elle ne prouverait rien."""
    src = (
        "class C:\n"
        "    def __init__(self, a=None, b=None):\n"
        "        self.a = a or require_secret('A')\n"
        "        self.b = b or get_secret('B', None)\n")
    init = src.split("class C:\n")[1]
    assert _hard_required_from_source(init) == {"a"}
