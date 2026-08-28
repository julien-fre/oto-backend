"""CLIQUET de la face REST : la table de routes SERVIE == la table attendue.

Pourquoi. La surface `/api/*` est le contrat avec le dashboard, l'extension, la
CLI, les fronts partenaires et les intégrations tierces. Rien ne la gardait : on
pouvait retirer un chemin, changer une méthode ou renommer un endpoint sans qu'un
seul test rougisse — la suite exerce des handlers, jamais la TABLE. Le trou s'est
vu le 2026-08-27, en découpant `api/routes.py` (1 370 lignes de `make_routes`, 52
handlers imbriqués) : la seule preuve disponible que la découpe ne changeait rien
a dû être fabriquée pour l'occasion, hors dépôt. Elle vit ici désormais.

Ce que ça attrape :
- un chemin **retiré** ou renommé → rouge (le client qui l'appelle, lui, ne teste
  pas ; il découvre en prod) ;
- une **méthode** perdue sur un chemin conservé (le `DELETE` qui disparaît d'un
  couple GET/POST/DELETE, le préflight `OPTIONS` oublié → CORS cassé au navigateur) ;
- l'**ordre** modifié : Starlette prend le PREMIER match, donc `…/tools/registry`
  DOIT précéder `…/tools/{name}`, sinon `registry` est avalé comme un nom d'outil ;
- un chemin **ajouté** → rouge aussi, exprès. Ajouter une route est légitime ;
  l'ajouter sans le dire ne l'est pas. Régénérer le fichier attendu EST la
  déclaration (le diff nomme le chemin, la revue le voit).

Ce que ça n'attrape pas : le COMPORTEMENT d'un handler. C'est le rôle du reste de
la suite — celui-ci ne garde que la forme de la surface.

Régénérer après un ajout ou une migration en capacité :

    python - <<'EOF'
    from oto_mcp.api import routes as api_routes
    import pathlib
    lignes = [f"{','.join(sorted(r.methods or []))} {r.path} -> {r.name}"
              for r in api_routes.make_routes(object())]
    pathlib.Path("tests/api/api_routes_table.txt").write_text("\\n".join(lignes) + "\\n")
    EOF
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
ATTENDU = pathlib.Path(__file__).resolve().parent / "api_routes_table.txt"


class _FakeVerifier:
    """`make_routes` ne fait que CAPTURER le verifier — il n'est jamais appelé au
    montage. Un objet nu suffit donc, et évite d'exiger Logto pour lire la table."""


def _servie() -> list[str]:
    from oto_mcp.api import routes as api_routes
    return [f"{','.join(sorted(getattr(r, 'methods', None) or []))} "
            f"{r.path} -> {r.name}"
            for r in api_routes.make_routes(_FakeVerifier(), mcp_instance=None)]


def test_table_de_routes_figee():
    servie = _servie()
    attendue = ATTENDU.read_text(encoding="utf-8").splitlines()
    manquantes = [l for l in attendue if l not in servie]
    ajoutees = [l for l in servie if l not in attendue]
    assert not manquantes, (
        f"{len(manquantes)} route(s) ne sont PLUS servies : {manquantes[:10]}\n"
        "Un chemin retiré casse ses appelants (dashboard, extension, CLI, fronts "
        "partenaires) sans qu'aucun autre test ne le voie. Si le retrait est "
        "voulu, retire la ligne de `tests/api/api_routes_table.txt` DANS LE MÊME "
        "commit et dis-le dans la PR.")
    assert not ajoutees, (
        f"{len(ajoutees)} route(s) NEUVES non déclarées : {ajoutees[:10]}\n"
        "Ajouter une route est légitime — l'ajouter en silence ne l'est pas. "
        "Régénère `tests/api/api_routes_table.txt` (recette dans le docstring) : le "
        "diff nomme le chemin, la revue le voit. Réflexe préalable : ce verbe "
        "doit-il naître CAPACITÉ (ADR 0042 §Convergence) plutôt que route écrite "
        "à la main ? cf. `test_rest_modules_are_capabilities.py`.")
    assert servie == attendue, (
        "L'ORDRE de la table a changé. Starlette prend le PREMIER match : "
        "`/api/me/tools/registry` doit précéder `/api/me/tools/{name}`, sinon "
        "`registry` est servi comme un nom d'outil. Rétablis l'ordre, ou "
        "régénère le fichier si le nouvel ordre est celui que tu veux servir.")


# --- Ordre des middlewares ASGI de la face REST -------------------------------
# `middleware/test_middleware_order.py` fige ceux de la face MCP (chaîne fastmcp). Les ASGI,
# posés par `server.py` sur l'app Starlette, ne l'étaient PAS — alors que des
# colonnes du journal en dépendent (docs/monitoring.md) : `RestCallLogger` est
# ajouté EN DERNIER pour être le plus EXTERNE, donc chronométrer toute la requête,
# `ViewAsMiddleware` compris. Posé plus interne, `duration_ms` cesserait de compter
# la résolution de consultation, et `org_id` serait lu avant d'être établi.
_ASGI = [
    "TenantChallengeMiddleware",      # 401 host-aware (ADR 0052 L3)
    "api_routes.ViewAsMiddleware",    # org/équipe/user de consultation (ADR 0023)
    "subdomain_org.SubdomainOrgMiddleware",  # org épinglée par le Host
    "api_routes.RestCallLogger",      # journal kind='rest' (ADR 0017) — le plus EXTERNE
]


def test_ordre_des_middlewares_asgi():
    """Relevé STATIQUE : les `add_middleware` vivent dans la fonction de service de
    `server.py`, pas au niveau module — les importer supposerait de booter."""
    tree = ast.parse((ROOT / "oto_mcp" / "server.py").read_text(encoding="utf-8"))
    # `app.add_middleware(...)` UNIQUEMENT : `mcp.add_middleware(...)` pose la chaîne
    # MCP, gardée par `middleware/test_middleware_order.py` — deux contrats distincts.
    poses = [ast.unparse(n.args[0]) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "add_middleware" and n.args
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "app"]
    assert poses == _ASGI, (
        f"Ordre des middlewares ASGI modifié : {poses}.\n"
        "Le DERNIER ajouté est le plus EXTERNE. `RestCallLogger` doit le rester : "
        "il chronomètre la requête entière et lit l'org de consultation. Relire "
        "docs/monitoring.md avant de changer quoi que ce soit.")
