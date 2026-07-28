"""Garde-fou SYMÉTRIQUE : une route REST de plateforme naît capacité, elle aussi.

`test_platform_tools_are_capabilities.py` (ADR 0042 §Convergence, Décision 4) ferme
un côté — un verbe de plateforme ne doit pas naître `@mcp.tool()` écrit à la main,
sinon la face REST devra être écrite une SECONDE fois, avec sa propre autz à tenir
en phase.

Il ne scanne que `oto_mcp/tools/` : un module **REST-only** (`api_routes_*.py`)
passait donc à travers, alors qu'il crée exactement la même dette en miroir — le
jour où l'agent en a besoin, on écrit un tool MCP à côté. Angle mort constaté le
2026-07-28 : `api_routes_zoho.py` a été ajouté à la main, le jour même où la
convergence était décidée, sans que rien ne le signale.

Ce test fige donc les modules REST écrits à la main. Comme son jumeau, **la liste
doit décroître** : un nouveau module casse la CI (réflexe attendu = déclarer une
capacité), et migrer un résidu casse aussi (retirer sa ligne).

⚠️ Ce que ce test ne prétend PAS : que tout endpoint soit exprimable en capacité.
Un **callback de redirection navigateur** (retour OAuth) ou un **webhook** répond
un 302 / un ACK à un tiers non authentifié — hors du contrat capacité (JSON +
autz). Ces routes-là sont marquées `True` (REST par nature) et restent légitimes.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"

# Modules REST écrits à la main. `True` = REST par NATURE (callback de redirection,
# webhook, endpoint consommé par un tiers) ; `False` = DETTE, à migrer en capacité.
_KNOWN: dict[str, bool] = {
    # Retours OAuth : le fournisseur redirige le NAVIGATEUR (302, sans en-tête
    # d'auth) — un contrat de capacité ne peut pas l'exprimer. Les verbes `start`/
    # `status`/`disconnect` qui les accompagnent, eux, sont de la dette.
    "api_routes_atlassian.py": True,
    "api_routes_folk.py": True,
    "api_routes_memento.py": True,
    "api_routes_zoho.py": True,
    # Webhook PSP (Mollie appelle, non authentifié côté Logto).
    "api_routes_billing.py": True,
    # Formulaire public du site vitrine (POST anonyme).
    "api_routes_contact.py": True,
    # DETTE — surfaces métier antérieures à la couche capacité.
    "api_routes_connectors.py": False,
    "api_routes_datastore.py": False,
    "api_routes_sirene.py": False,
    "api_routes_accords.py": False,
}


def _rest_modules() -> set[str]:
    """Modules `api_routes_*.py` qui déclarent réellement des `Route(...)`."""
    out: set[str] = set()
    for path in sorted(ROOT.glob("api_routes_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Route"):
                out.add(path.name)
                break
    return out


def test_no_new_handwritten_rest_module():
    found = _rest_modules()
    unexpected = sorted(found - set(_KNOWN))
    assert not unexpected, (
        f"Modules REST écrits à la main hors liste connue : {unexpected}. "
        "Déclare le verbe comme une CAPACITÉ (`oto_mcp/capabilities/`) : les "
        "adaptateurs en dérivent les faces MCP et REST depuis un descripteur "
        "unique, avec UNE autz — cf. ADR 0042 §Convergence des surfaces. "
        "Exception admise : un callback de redirection ou un webhook, à déclarer "
        "ici avec `True` et sa raison.")
    gone = sorted(set(_KNOWN) - found)
    assert not gone, (
        f"Ces modules REST n'existent plus (ou n'ont plus de Route) : {gone}. "
        "Retire-les de `_KNOWN` — la liste doit décroître, jamais mentir.")


def test_debt_list_is_explicit():
    """La dette est NOMMÉE, pas noyée : on doit pouvoir la compter d'un coup d'œil
    (principe « no silent caps » — un plafond tu, c'est un plafond oublié)."""
    debt = sorted(m for m, by_nature in _KNOWN.items() if not by_nature)
    assert debt, "plus aucune dette REST déclarée — mets à jour ce test"
    assert len(debt) <= 4, (
        f"la dette REST a grossi : {debt}. Elle doit DÉCROÎTRE (migration en "
        "capacités), jamais s'étendre.")
