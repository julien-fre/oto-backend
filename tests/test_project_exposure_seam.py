"""Les opt-ins d'exposition d'un projet publié : UNE garde, lue par les DEUX faces (#557).

Un projet publié sans login est servi par la même URL sur deux faces — le serveur MCP et
l'UI web navigable. Tant que chacune décidait pour elle-même, elles ont divergé sans le
montrer : la face MCP exigeait `secret` + `mcp_expose_docs` avant de servir les pages, la
face web les listait et rendait leur corps entier sans consulter aucun flag. Trois projets
publiés en production servaient ainsi 9 pages internes à qui ouvrait l'URL.

Ce fichier tient les deux bouts :
1. la table de vérité du seam (`project_exposure`), pure et lisible d'un coup d'œil ;
2. un cliquet STRUCTUREL — hors du seam, plus personne ne relit ces colonnes. Une
   troisième face (un export, une preview, un flux) qui rouvrirait sa propre version de
   la règle rougit ici, au lieu de diverger en silence pendant des mois.
"""
from __future__ import annotations

import ast
import pathlib

from oto_mcp import project_exposure as px

_PKG = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"

_SECRET_ALL = {"mcp_access": "secret", "mcp_expose_docs": True,
               "mcp_expose_datastore": True, "mcp_expose_datastore_write": True}


# ── 1. La table de vérité ─────────────────────────────────────────────────────
def test_pages_need_secret_and_the_explicit_optin():
    assert px.docs_exposed(_SECRET_ALL) is True
    # L'opt-in manquant ou faux : refus. C'est un opt-in, pas un défaut.
    assert px.docs_exposed({"mcp_access": "secret"}) is False
    assert px.docs_exposed({**_SECRET_ALL, "mcp_expose_docs": False}) is False
    # `anonymous` est LISTÉ dans l'annuaire public : le flag n'y rachète rien.
    assert px.docs_exposed({**_SECRET_ALL, "mcp_access": "anonymous"}) is False
    assert px.docs_exposed({**_SECRET_ALL, "mcp_access": "org"}) is False
    assert px.docs_exposed({}) is False


def test_datastore_read_needs_secret_and_its_own_optin():
    assert px.datastore_exposed(_SECRET_ALL) is True
    assert px.datastore_exposed({"mcp_access": "secret"}) is False
    assert px.datastore_exposed({**_SECRET_ALL, "mcp_access": "anonymous"}) is False


def test_datastore_write_is_additional_and_never_stands_alone():
    assert px.datastore_writable(_SECRET_ALL) is True
    # L'écriture ne survit pas à une lecture refermée : elle en dépend.
    assert px.datastore_writable({**_SECRET_ALL, "mcp_expose_datastore": False}) is False
    assert px.datastore_writable({"mcp_access": "secret",
                                  "mcp_expose_datastore_write": True}) is False


def test_the_three_optins_are_independent_of_each_other():
    """Les pages et le datastore ont des régimes INVERSES à la publication (le datastore
    est le livrable qu'on partage, les pages sont de la doc interne) : exposer l'un ne
    doit jamais entraîner l'autre."""
    ds_only = {"mcp_access": "secret", "mcp_expose_datastore": True}
    assert px.datastore_exposed(ds_only) is True and px.docs_exposed(ds_only) is False
    docs_only = {"mcp_access": "secret", "mcp_expose_docs": True}
    assert px.docs_exposed(docs_only) is True and px.datastore_exposed(docs_only) is False


# ── 2. Le cliquet structurel ──────────────────────────────────────────────────
# Les trois colonnes d'opt-in. Les relire AILLEURS que dans le seam, c'est réécrire la
# règle une seconde fois — la faute exacte de #557.
_OPTIN_COLUMNS = {"mcp_expose_docs", "mcp_expose_datastore", "mcp_expose_datastore_write"}

# Une entrée ici est une DÉCISION, avec sa raison. Aucune de ces trois n'est une face de
# service : ce sont la définition, la persistance et le contrat d'entrée.
_ALLOWED = {
    "project_exposure.py": "le seam lui-même — la seule définition de la règle",
    "db/projects.py": "persistance : l'UPDATE qui écrit les colonnes",
    "capabilities/projects.py": "contrat d'entrée : les champs de `publish_mcp`",
}


def _string_constants(tree: ast.AST) -> set[str]:
    """Toutes les chaînes littérales d'un module, SAUF les docstrings et autres chaînes
    posées en instruction nue (commenter la règle n'est pas la réécrire)."""
    bare = {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in bare}


def test_only_the_seam_reads_the_optin_columns():
    offenders: dict[str, set[str]] = {}
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_PKG).as_posix()
        if rel in _ALLOWED:
            continue
        hits = _string_constants(ast.parse(path.read_text(encoding="utf-8"))) & _OPTIN_COLUMNS
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        f"ces modules relisent les colonnes d'opt-in hors du seam : {offenders}. "
        f"La règle « ce projet expose-t-il ses pages / son datastore ? » a UNE "
        f"définition, `oto_mcp/project_exposure.py`, et les faces l'appellent. En "
        f"écrire une deuxième version, c'est #557 : deux gardes qui divergent sans "
        f"que rien ne le montre. Si l'accès est légitime (persistance, contrat "
        f"d'entrée), déclare-le dans `_ALLOWED` avec sa raison.")


def test_both_faces_call_the_seam():
    """Le pendant positif : les deux modules servants appellent bien `project_exposure`.
    Sans ça, l'interdit ci-dessus serait satisfait par un module qui ne garde RIEN."""
    for rel, expected in (("subdomain_project.py", {"docs_exposed", "datastore_exposed",
                                                    "datastore_writable"}),
                          ("share_ui.py", {"docs_exposed", "datastore_exposed"})):
        tree = ast.parse((_PKG / rel).read_text(encoding="utf-8"))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and isinstance(n.func.value, ast.Name)
                  and n.func.value.id == "project_exposure"}
        assert expected <= called, (
            f"`{rel}` n'appelle pas {sorted(expected - called)} sur `project_exposure` — "
            f"une face qui décide seule est une face qui divergera.")
