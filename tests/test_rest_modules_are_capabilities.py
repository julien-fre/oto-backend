"""Garde-fou SYMÉTRIQUE : une route REST de plateforme naît capacité, elle aussi.

`test_platform_tools_are_capabilities.py` (ADR 0042 §Convergence, Décision 4) ferme
un côté — un verbe de plateforme ne doit pas naître `@mcp.tool()` écrit à la main,
sinon la face REST devra être écrite une SECONDE fois, avec sa propre autz à tenir
en phase.

Il ne scanne que `oto_mcp/tools/` : une route **REST-only** passait donc à travers,
alors qu'elle crée la même dette en miroir — le jour où l'agent en a besoin, on
écrit un tool MCP à côté. Angle mort constaté le 2026-07-28 (`api_routes_zoho.py`
ajouté à la main le jour même de la convergence, sans que rien ne le signale).

**Grain = la ROUTE, pas le module.** Première version classée par module : un seul
webhook « par nature » y blanchissait les 17 autres routes du même fichier. La
plupart des modules sont mixtes (un callback OAuth + dix verbes de dashboard), donc
seule la route est classifiable.

Trois natures :
- `NATURE` — un tiers appelle, hors contrat capacité : **callback** de redirection
  (302, sans auth), **webhook**, ou **API consommée par un programme externe**
  (oto-core/oto-cli), dont le chemin est un contrat gelé.
- `DEBT` — verbe de dashboard/agent écrit à la main : à migrer en capacité.
- absente de la liste — nouvelle route : la CI casse (réflexe = déclarer une capacité).

La liste DEBT doit décroître, jamais s'étendre.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"

NATURE, DEBT = "nature", "debt"

_KNOWN: dict[str, str] = {
    # --- Retours de consentement OAuth : le fournisseur redirige le NAVIGATEUR
    # (302, sans en-tête d'auth). Hors contrat capacité (JSON + autz).
    "/api/zoho/oauth/callback": NATURE,
    "/api/google/oauth/callback": NATURE,
    "/api/folkmcp/oauth/callback": NATURE,
    "/api/atlassian/oauth/callback": NATURE,
    "/api/salesforce/oauth/callback": NATURE,
    # --- Webhooks : un tiers appelle, non authentifié côté Logto.
    "/api/unipile/webhook": NATURE,
    "/api/billing/webhook": NATURE,
    # --- Formulaire public du site vitrine (POST anonyme).
    "/api/contact": NATURE,
    # --- APIs consommées par un PROGRAMME externe (oto-core / oto-cli), chemins
    # gelés par contrat : `SireneStock` HTTP client, repli CLI des accords quand le
    # transport MCP est indisponible. Un tool MCP existe en parallèle, mais c'est un
    # CONNECTEUR (`fr_*`), pas un verbe de plateforme — pas la dette visée ici.
    "/api/sirene/headquarters": NATURE,
    "/api/sirene/siege": NATURE,
    "/api/sirene/etablissements": NATURE,
    "/api/sirene/siret": NATURE,
    "/api/sirene/search": NATURE,
    "/api/sirene/info": NATURE,
    "/api/fr/accords/search": NATURE,
    "/api/fr/accords/themes": NATURE,
    "/api/fr/accords/{id_or_numero}": NATURE,

    # --- DETTE : verbes de dashboard écrits à la main, à migrer en capacités.
    # Connexion hébergée + gouvernance connecteur.
    "/api/me/unipile": DEBT,
    "/api/me/unipile/connect": DEBT,
    "/api/me/unipile/reconcile": DEBT,
    "/api/admin/unipile/seats": DEBT,
    "/api/admin/connectors/activation": DEBT,
    "/api/admin/connectors/{provider}/platform-access": DEBT,
    # Datastore : miroir REST des tools `data_*` (eux-mêmes listés en dette dans
    # le garde-fou jumeau) — deux implémentations du même métier.
    "/api/datastore/namespaces": DEBT,
    "/api/datastore/namespaces/{namespace}": DEBT,
    "/api/datastore/namespaces/{namespace}/aggregate": DEBT,
    "/api/datastore/namespaces/{namespace}/queue": DEBT,
    "/api/datastore/namespaces/{namespace}/rows": DEBT,
    "/api/datastore/namespaces/{namespace}/rows/{row_id}": DEBT,
    "/api/datastore/namespaces/{namespace}/rows/{row_id}/activity": DEBT,
    "/api/datastore/namespaces/{namespace}/rows/{row_id}/release": DEBT,
    "/api/datastore/namespaces/{namespace}/schema": DEBT,
    "/api/datastore/namespaces/{namespace}/share": DEBT,
    "/api/datastore/namespaces/{namespace}/url": DEBT,
    # OAuth Google : les VERBES (le callback ci-dessus est, lui, par nature).
    "/api/google/oauth": DEBT,
    "/api/google/oauth/start": DEBT,
    "/api/google/oauth/status": DEBT,
    "/api/google/oauth/default": DEBT,
    # Jetons CLI/API de l'utilisateur.
    "/api/me/tokens": DEBT,
    "/api/me/tokens/{token_id}": DEBT,
    # Fédération MCP per-user (mêmes verbes répétés par connecteur fédéré).
    "/api/atlassian/oauth/start": DEBT,
    "/api/atlassian/oauth/status": DEBT,
    "/api/atlassian/oauth": DEBT,
    "/api/folkmcp/oauth/start": DEBT,
    "/api/folkmcp/oauth/status": DEBT,
    "/api/folkmcp/oauth": DEBT,
}


def _handwritten_routes() -> dict[str, str]:
    """`{chemin: module}` de toute `Route("…")` déclarée dans un `api_routes_*.py`."""
    out: dict[str, str] = {}
    for path in sorted(ROOT.glob("api_routes_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Route" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                out[node.args[0].value] = path.name
    return out


def test_no_new_handwritten_rest_route():
    found = _handwritten_routes()
    unexpected = sorted(p for p in found if p not in _KNOWN)
    assert not unexpected, (
        f"Routes REST écrites à la main hors liste connue : {unexpected}. "
        "Déclare le verbe comme une CAPACITÉ (`oto_mcp/capabilities/`) : les "
        "adaptateurs en dérivent les faces MCP et REST depuis un descripteur "
        "unique, avec UNE autz — cf. ADR 0042 §Convergence des surfaces. "
        "Exception admise (callback de redirection, webhook, API consommée par un "
        "programme externe) : à déclarer ici en `NATURE`, avec sa raison.")
    gone = sorted(p for p in _KNOWN if p not in found)
    assert not gone, (
        f"Ces routes n'existent plus : {gone}. Retire-les de `_KNOWN` — la liste "
        "doit refléter le réel, jamais mentir.")


def test_rest_debt_only_shrinks():
    """La dette est NOMMÉE et COMPTÉE (« no silent caps » : un plafond tu est un
    plafond oublié). Ce plafond ne doit que baisser, au fil des migrations."""
    debt = sorted(p for p, kind in _KNOWN.items() if kind == DEBT)
    assert len(debt) <= 37, (
        f"la dette REST a grossi ({len(debt)} routes) : {debt}. Elle doit "
        "DÉCROÎTRE — migre en capacité plutôt que d'élargir le plafond.")


def test_zoho_start_and_modes_are_capabilities_not_routes():
    """Régression de la migration du jour : ces deux verbes ont quitté le REST
    écrit à la main pour `capabilities/zoho_connect.py` (le callback, lui, reste)."""
    routes = _handwritten_routes()
    assert "/api/zoho/oauth/start" not in routes
    assert "/api/zoho/oauth/modes" not in routes
    assert "/api/zoho/oauth/callback" in routes
