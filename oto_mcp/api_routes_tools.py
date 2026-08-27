"""Handlers de la TOOLBOX du membre — ce que le dashboard montre et pilote des
outils MCP exposés à l'appelant.

- `GET /api/me/tools`                 → tous les tools + leur état (activé/désactivé)
- `GET /api/me/tools/registry`        → registre résolu (ADR 0014), matière des refs `<tool:slug>`
- `POST|DELETE /api/me/tools/{name}`  → (dés)activation VISIBILITÉ-ONLY (ADR 0031)
- `GET /api/me/tools/{name}/detail`   → fiche : description + schémas + connecteur
- `POST /api/me/tools/{name}/call`    → exécute un outil TESTABLE sous l'identité de l'appelant

⚠️ Ces cinq chemins sont le miroir REST d'`oto_list_my_tools` / `oto_enable_tool` /
`oto_disable_tool` / `oto_tool_schema` / `oto_call` : deux implémentations du même
métier, exactement la dette que nomme `test_rest_modules_are_capabilities.py`. Les
regrouper ne la rembourse pas — ça la rend lisible d'un bloc, donc migrable d'un
bloc.

`mcp_instance` était une variable de closure ; c'est désormais un paramètre nommé.
`run_middleware=False` partout où l'on liste : on est hors session MCP (contexte
REST), la chaîne de middleware n'a pas de `Context` FastMCP et lèverait.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api_routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import access, auth_hooks, connectors, db, tool_registry
from .api_routes_base import _authenticate, _json, _json_error
from .tool_visibility import (
    PROTECTED_TOOLS, is_default_hidden, is_testable, namespace_of)


async def my_tools_list(request: Request, *, verifier: JWTVerifier, mcp_instance) -> JSONResponse:
    """Liste tous les tools du serveur avec l'état (enabled/disabled)
    pour l'utilisateur courant.
    """
    sub, err = await _authenticate(request, verifier)
    if err:
        return err

    all_names: set[str] = set()
    if mcp_instance is not None:
        # run_middleware=False : appelé hors session MCP (contexte REST), la
        # chaîne de middleware n'a pas de Context FastMCP et lèverait → on
        # veut la liste statique complète, le filtrage disabled est fait
        # juste après via `disabled`. (cf. _list_all_tool_names)
        tools = await mcp_instance.list_tools(run_middleware=False)
        all_names = {t.name for t in tools}

    disabled = set(db.list_user_disabled_tools(sub, access.current_org(sub) or 0))
    # Le middleware retire déjà les disabled de `list_tools` selon le sub
    # courant (celui de la requête REST = même token). On ré-ajoute donc
    # les disabled pour avoir la vue complète.
    all_names |= disabled

    return _json(request, {
        "tools": [
            {"name": n, "enabled": n not in disabled,
             "protected": n in PROTECTED_TOOLS}
            for n in sorted(all_names)
        ],
    })


async def my_tools_registry(request: Request, *, verifier: JWTVerifier, mcp_instance) -> JSONResponse:
    """Registre résolu des tools exposés (ADR 0014) : nom + description
    (1ʳᵉ ligne de la docstring = champ MCP `description`, source de vérité du
    modèle) + source `native`/`federated`. Alimente la résolution des
    marqueurs `<tool:slug>` d'une doctrine, l'autocomplétion et le manifeste
    « outils référencés ». Les namespaces grant-only (bridges) sont exclus."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    try:
        reg = await tool_registry.build_registry(mcp_instance)
    except Exception as e:
        return _json_error(request, 500, f"list_tools_failed:{e}")
    out = sorted(reg.values(), key=lambda e: e["name"])
    return _json(request, {"tools": out, "count": len(out)})


async def my_tools_disable(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    """Désactive un tool pour l'utilisateur courant (live)."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    name = request.path_params["name"]
    if name in PROTECTED_TOOLS:
        return _json_error(request, 400, f"protected_tool:{name}")
    org = access.current_org(sub) or 0
    db.add_user_disabled_tool(sub, name, org)
    db.remove_user_enabled_tool(sub, name, org)  # lève un éventuel override positif
    return _json(request, {"ok": True, "name": name, "enabled": False})


async def my_tools_enable(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    """Réactive un tool pour l'utilisateur courant (live).

    Visibilité-only (ADR 0031) — même modèle que le meta-tool `oto_enable_tool` :
    activer = préférence d'affichage, pas une autorisation (accès réel gardé au
    call-time : credential + require_connector_access ADR 0025 + activation).
    """
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    name = request.path_params["name"]
    org = access.current_org(sub) or 0
    db.remove_user_disabled_tool(sub, name, org)
    # Override positif requis pour rendre visible un masqué-par-défaut.
    if is_default_hidden(name):
        db.add_user_enabled_tool(sub, name, org)
    return _json(request, {"ok": True, "name": name, "enabled": True})


async def _tool_by_name(name: str, mcp_instance):
    """Objet Tool FastMCP par nom (ou None). `run_middleware=False` : hors
    session MCP (contexte REST) la chaîne de middleware n'a pas de Context."""
    if mcp_instance is None:
        return None
    tools = await mcp_instance.list_tools(run_middleware=False)
    for t in tools:
        if t.name == name:
            return t
    return None


async def my_tool_detail(request: Request, *, verifier: JWTVerifier, mcp_instance) -> JSONResponse:
    """Fiche d'un outil : description complète + schémas d'entrée/sortie
    (JSON Schema dérivé par FastMCP) + connecteur + état perso + testabilité.

    Alimente le panneau « en savoir plus » de la fiche connecteur (dashboard) —
    détail utile pour comprendre un outil (surtout open-data FOD) et, s'il est
    testable, générer un formulaire de test."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    name = request.path_params["name"]
    tool = await _tool_by_name(name, mcp_instance)
    if tool is None:
        return _json_error(request, 404, f"unknown_tool:{name}")
    ns = namespace_of(name)
    conn = connectors.connector_for_namespace(ns)
    disabled = set(db.list_user_disabled_tools(sub, access.current_org(sub) or 0))
    federated = bool(conn and conn.kind == "mount")
    return _json(request, {
        "name": name,
        "description": (tool.description or "").strip(),
        "input_schema": getattr(tool, "parameters", None),
        "output_schema": getattr(tool, "output_schema", None),
        "namespace": ns,
        "connector": ({"name": conn.name, "label": conn.label} if conn else None),
        "source": "federated" if federated else "native",
        "enabled": name not in disabled,
        "protected": name in PROTECTED_TOOLS,
        "default_hidden": is_default_hidden(name),
        "testable": is_testable(name),
    })


async def my_tool_call(request: Request, *, verifier: JWTVerifier, mcp_instance) -> JSONResponse:
    """Exécute un outil TESTABLE sous l'identité de l'appelant (bouton « tester »
    du dashboard). Bornée aux connecteurs open-data en lecture seule
    (`is_testable`) — jamais un outil à effet de bord. Les gates de call-time
    (credential, RBAC connecteur, activation) s'appliquent normalement : le
    sub-override REST fait résoudre la bonne identité (`resolve_api_key`/
    `current_org`). L'erreur d'un outil est renvoyée EN DONNÉE (`ok:false`) —
    voir ce que renvoie l'outil (y compris son erreur) EST le but du test."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    name = request.path_params["name"]
    if not is_testable(name):
        return _json_error(request, 403, f"not_testable:{name}")
    tool = await _tool_by_name(name, mcp_instance)
    if tool is None:
        return _json_error(request, 404, f"unknown_tool:{name}")
    fn = getattr(tool, "fn", None)
    if fn is None:
        return _json_error(request, 400, f"not_callable:{name}")
    try:
        body = await request.json()
    except Exception:
        body = {}
    # Accepte {"arguments": {...}} ou l'objet d'arguments brut.
    args = body.get("arguments") if isinstance(body, dict) and "arguments" in body else body
    if not isinstance(args, dict):
        args = {}

    async def _invoke():
        if asyncio.iscoroutinefunction(fn):
            return await fn(**args)
        return await run_in_threadpool(lambda: fn(**args))

    started = time.monotonic()
    with auth_hooks.sub_override(sub):
        try:
            result = await asyncio.wait_for(_invoke(), timeout=45)
        except asyncio.TimeoutError:
            return _json(request, {"ok": False, "name": name,
                                   "error": "timeout (>45s)"})
        except TypeError as e:
            # Mauvais arguments (param inconnu / manquant) : signal actionnable.
            return _json_error(request, 400, f"bad_arguments:{e}")
        except Exception as e:  # noqa: BLE001 — l'erreur d'outil est le résultat
            return _json(request, {"ok": False, "name": name, "error": str(e)})
    elapsed_ms = int((time.monotonic() - started) * 1000)
    # Sérialisation défensive : un tool peut renvoyer un objet non-JSON.
    try:
        safe = json.loads(json.dumps(result, default=str, ensure_ascii=False))
    except Exception:
        safe = str(result)
    return _json(request, {"ok": True, "name": name, "result": safe,
                           "elapsed_ms": elapsed_ms})
