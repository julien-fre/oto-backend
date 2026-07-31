"""Garde-fou version-skew (otomata-private, leçon folk_get_user) — un tool ne doit
PAS référencer une méthode absente de l'oto-core ÉPINGLÉ.

Contexte : backend et oto-core sont deux repos ; le backend épingle une version
d'oto-core (pin git dans pyproject, ADR 0020). Un tool mergé en avance de phase —
qui appelle `client.methode()` avant que le tag épinglé la contienne — passe la
CI (l'import du module réussit, la méthode n'est touchée qu'à l'appel) puis lève
`AttributeError` en prod à la 1ʳᵉ invocation (vécu 2026-07-01→03 : `folk_get_user`
→ `FolkClient` sans `get_user` sur v1.11.0, corrigé par le bump v1.12.0).

Cette sonde ferme la fenêtre : en CI de PR, oto-core est installé AU TAG ÉPINGLÉ
(runner neuf → pin du pyproject) ; on vérifie STATIQUEMENT que chaque `_client().m()`
d'un tool existe sur la vraie classe. Une méthode manquante casse la PR au lieu
d'atteindre la prod.

Portée = `def _client(...) -> <ClasseConcrète>` **ou** `-> tuple[<Classe>, …]`, et
les appels sur le client, qu'il soit chaîné (`_client().m()`), lié à une variable
(`client, _ = _client()` puis `client.m()`) ou passé à un dispatcher (`c.m()`).

⚠️ La portée a été élargie le 2026-07-31 après un trou vécu : `tools/apollo.py`
déclare `def _client() -> tuple[ApolloClient, bool]` et appelle `client.m()` — les
DEUX motifs échappaient à la sonde, donc **le module entier sortait de la couverture
en silence**. Un `apollo_bulk_enrich_organizations` appelant une méthode absente de
l'oto-core épinglé serait passé en CI pour lever `AttributeError` en prod : très
exactement ce que ce fichier existe pour empêcher. Le docstring promettait que les
modules hors convention seraient « listés » — personne ne les listait ; c'est
maintenant un test (`test_no_module_silently_uncovered`)."""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "oto_mcp" / "tools"

# Modules sans `_client()` du tout (spine, dispatchers, connecteurs sans client
# oto-core) : hors sujet, pas un trou. Tout AUTRE module non couvert casse le test
# de couverture — la sonde ne doit jamais rétrécir sans que ça se voie.
_NO_CLIENT_EXPECTED = {
    "meta", "whoami", "docs_app", "datastore", "remote", "mount",
}

# Clients à SOUS-OBJETS (`client.companies.list(…)`) : leurs namespaces sont des
# attributs d'instance, invérifiables sur la classe → hors de portée de cette sonde
# statique. Déclarés ici pour que ça reste un choix visible, pas un oubli.
_SUBOBJECT_CLIENTS = {"attio"}

# Dispatch DYNAMIQUE (`getattr(client, method)(**kw)`) : le nom de la méthode est une
# donnée, pas du code — invérifiable statiquement. Ces connecteurs restent donc à
# découvert sur le version-skew ; c'est un choix, pas un oubli, et l'ajouter à cette
# liste doit rester un geste conscient (sinon un nouveau connecteur perdrait sa
# couverture en silence, ce qui est précisément le trou qu'on vient de fermer).
_DYNAMIC_DISPATCH_CLIENTS = {"serper", "serpapi", "brightdata", "cloro", "spott"}


def _client_class_name(tree: ast.Module) -> str | None:
    """Classe concrète rendue par `_client()` — annotation `Name` (`-> FolkClient`)
    ou premier élément d'un `tuple[...]` (`-> tuple[ApolloClient, bool]`, la forme
    des connecteurs qui rendent aussi « clé plateforme ? »). None si absente."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_client"):
            continue
        ret = node.returns
        if isinstance(ret, ast.Name):
            return ret.id
        # tuple[ApolloClient, bool] → ApolloClient
        if isinstance(ret, ast.Subscript):
            base = ret.value
            if isinstance(base, ast.Name) and base.id in ("tuple", "Tuple"):
                sl = ret.slice
                elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
                if elts and isinstance(elts[0], ast.Name):
                    return elts[0].id
    return None


def _names_bound_to_client(tree: ast.Module) -> set[str]:
    """Variables qui REÇOIVENT le client : `client = _client()` et le dépaquetage
    `client, is_platform = _client()`. Sans ça, tout connecteur qui passe par une
    variable (au lieu de chaîner) est invisible à la sonde."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if not (isinstance(val, ast.Call) and isinstance(val.func, ast.Name)
                and val.func.id == "_client"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Tuple) and target.elts:
                # 1er élément = le client (le reste = is_platform…)
                first = target.elts[0]
                if isinstance(first, ast.Name):
                    names.add(first.id)
    return names


def _import_of(tree: ast.Module, clsname: str) -> str | None:
    """Module d'origine d'un `from <module> import <clsname>` (n'importe où, y
    compris imports imbriqués dans `register`)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == clsname:
                    return node.module
    return None


def _methods_called_on_client(tree: ast.Module) -> set[str]:
    """Méthodes appelées sur le client, quelle que soit la façon de le tenir :

    - **chaîné** `_client().m()` ;
    - **lié** `client, _ = _client()` puis `client.m()` (cf. `_names_bound_to_client`) ;
    - **passé** `_create_one(c, …)` puis `c.m()` — nom conventionnel du client dans
      un dispatcher partagé (ex. Folk factorise singulier/bulk) ; sans ce motif, un
      connecteur qui factorise perd toute couverture version-skew.

    ⚠️ Seuls les attributs **appelés** comptent (`client.m(...)`), pas les accès nus.
    Un client à sous-objets (`client.companies.list(…)`, Attio) porte ses namespaces
    en attributs d'INSTANCE : `hasattr(AttioClient, "companies")` est False sur la
    classe, donc les compter produirait un faux « méthode absente » — un garde-fou
    qui crie à tort finit ignoré.
    """
    bound = _names_bound_to_client(tree) | {"c"}
    methods: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        recv = node.func.value
        if (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name)
                and recv.func.id == "_client"):
            methods.add(node.func.attr)
        elif isinstance(recv, ast.Name) and recv.id in bound:
            methods.add(node.func.attr)
    return methods


def _covered_modules() -> list[tuple[str, str, str, set[str]]]:
    """(module_tool, clsname, import_module, méthodes) pour chaque tool suivant la
    convention `_client() -> ClasseConcrète` avec ≥1 appel `_client().m()`."""
    out = []
    for path in sorted(_TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        cls = _client_class_name(tree)
        if not cls:
            continue
        methods = _methods_called_on_client(tree)
        if not methods:
            continue
        mod = _import_of(tree, cls)
        if not mod:
            continue
        out.append((path.stem, cls, mod, methods))
    return out


_CASES = _covered_modules()


def test_convention_coverage_not_silently_shrinking():
    """Filet anti-régression de couverture : si ce nombre chute brutalement (un
    connecteur bascule hors convention), la sonde couvre moins sans le dire."""
    covered = {c[0] for c in _CASES}
    assert "folk" in covered, "folk doit rester couvert (cas d'école du garde-fou)"
    assert "apollo" in covered, (
        "apollo doit rester couvert : `_client() -> tuple[...]` + `client.m()` est "
        "le motif qui échappait à la sonde (trou vécu 2026-07-31)")
    assert len(_CASES) >= 20, f"couverture anormalement basse ({len(_CASES)} modules)"


def test_no_module_silently_uncovered():
    """Un module de connecteur qui a un `_client()` mais sort de la portée de la
    sonde doit se VOIR. C'est le trou qui a laissé passer apollo : le docstring
    promettait que les modules hors convention seraient listés, rien ne le faisait,
    donc perdre la couverture d'un connecteur ne coûtait rien."""
    covered = {c[0] for c in _CASES}
    uncovered = []
    for path in sorted(_TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_") or path.stem in covered:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        has_client = any(isinstance(n, ast.FunctionDef) and n.name == "_client"
                         for n in ast.walk(tree))
        exempt = _NO_CLIENT_EXPECTED | _SUBOBJECT_CLIENTS | _DYNAMIC_DISPATCH_CLIENTS
        if has_client and path.stem not in exempt:
            cls = _client_class_name(tree)
            why = ("pas de méthode détectée sur le client" if cls
                   else "type de retour de `_client()` non reconnu")
            uncovered.append(f"{path.stem} ({why})")
    assert not uncovered, (
        "modules avec un `_client()` mais SANS couverture version-skew : "
        f"{uncovered} — élargis la sonde (ou déclare-les dans _NO_CLIENT_EXPECTED "
        "si le client ne vient pas d'oto-core).")


@pytest.mark.parametrize("tool_mod, clsname, import_mod, methods",
                         _CASES, ids=[c[0] for c in _CASES])
def test_client_methods_exist_on_pinned_core(tool_mod, clsname, import_mod, methods):
    """Chaque `_client().m()` du tool existe sur la classe oto-core épinglée."""
    try:
        cls = getattr(importlib.import_module(import_mod), clsname)
    except Exception as e:  # noqa: BLE001 — extra non installé, etc.
        pytest.skip(f"{clsname} non importable ({import_mod}) : {e}")
    missing = sorted(m for m in methods if not hasattr(cls, m))
    assert not missing, (
        f"{tool_mod}.py appelle des méthodes absentes de {clsname} "
        f"(oto-core épinglé) : {missing} — bump le pin oto-core dans CETTE PR "
        f"(version-skew, cf. leçon folk_get_user).")
