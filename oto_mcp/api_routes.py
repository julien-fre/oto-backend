"""REST API consommée par le frontend oto.ninja (page de gestion de compte).

Endpoints (ce fichier — gestion compte, providers,
tools, admin, WhatsApp) :
- `GET    /api/me`                            → infos user + rôle + statut keys
- `GET    /api/settings/api-keys/{provider}`  → état/clé (tout connecteur byo_user à secret simple)
- `POST   /api/settings/api-keys/{provider}`  → pose le credential : `api_key`→`{key}` ; `basic_auth`→`{email,password}`
- `DELETE /api/settings/api-keys/{provider}`  → efface
- `GET    /api/me/tools` + `POST/DELETE /api/me/tools/{name}` → toggle tools per-user
- `GET    /api/admin/*`                       → admin (users, platform-keys, grants, tokens)

Endpoints datastore / Google OAuth / API tokens : voir `api_routes_datastore.py`.
Endpoints SIRENE stock : voir `api_routes_sirene.py`.
Endpoints organisation (`/api/me/orgs`, `/api/orgs/*`, `/api/admin/orgs/*`,
`/api/admin/namespace-grants*`) : voir `api_routes_orgs.py` — projection REST du
palier org (mêmes fonctions de service que les meta-tools MCP `oto_admin_*org*`).

Auth : Bearer JWT Logto **ou** API token long-lived (préfixe `oto_`), vérifié
via `_authenticate`. Le frontend obtient le token Logto via `@logto/vue`. La
CLI utilise un API token issu sur `/account` (stocké en SOPS sous `OTO_API_KEY`).

CORS : limité aux origines oto.ninja (+ localhost en dev).
"""
from __future__ import annotations

import os
from typing import Iterable

import asyncio
import base64
import html as _html
import json
import logging
import re
import time

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                                  Response, StreamingResponse)

from . import access, api_routes_accords, api_routes_atlassian, api_routes_billing, api_routes_connectors, api_routes_contact, api_routes_datastore, api_routes_folk, api_routes_salesforce, api_routes_sirene, api_routes_zoho, billing, connector_activation, connectors, credentials_store, db, doc_export, group_store, openapi, org_store, ownership, token_scopes, tool_registry
from .capabilities import _rest_adapter as _cap_rest_adapter
from .capabilities import registry as _cap_registry
from . import auth_hooks, guide_store, tenancy
from .tool_visibility import (
    PROTECTED_TOOLS, is_default_hidden, is_testable, namespace_of)
# Primitives partagées (auth, CORS, réponses JSON, préflight, `bind`) : elles ont
# quitté ce fichier pour `api_routes_base.py` le 2026-08-27, sous les modules de
# domaine qui les appellent (sinon l'import serait circulaire). RÉ-EXPORTÉES ici :
# `api_routes._authenticate` / `_cors_headers` / `_json` … restent valides.
from .api_routes_base import (  # noqa: F401 — ré-export de compatibilité
    AuthFn, _allowed_origins, _authenticate, _cors_headers, _json, _json_error,
    _maybe_view_as, bind, options_handler)
# Handlers par DOMAINE (découpe du 2026-08-27) : chaque module porte des fonctions
# de module, testables seules ; la table de routes ci-dessous reste ici.
from . import api_routes_public as public
from . import api_routes_account as account
from . import api_routes_media as media
from . import api_routes_projects as projects
from . import api_routes_uploads as uploads
from . import api_routes_credentials as credentials

logger = logging.getLogger(__name__)




# ── View-as (ADR 0023) : consultation d'une org dans le dashboard ───────────
def _parse_view_org(request: Request) -> int | None:
    """Org de consultation (header `X-Oto-Org`). None = pas de header ; 0 = perso ;
    >0 = id d'org. Header mal formé → None (repli maison, jamais d'erreur dure)."""
    raw = request.headers.get("x-oto-org")
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in ("", "0", "perso", "personal"):
        return 0
    try:
        n = int(v)
        return n if n > 0 else 0
    except ValueError:
        return None


def _parse_view_group(request: Request) -> int | None:
    """Équipe de consultation (header `X-Oto-Group`). None = pas de header / niveau
    org ; >0 = id de groupe. Pas de sentinelle perso (l'absence = niveau org)."""
    raw = request.headers.get("x-oto-group")
    if raw is None:
        return None
    try:
        n = int(raw.strip())
        return n if n > 0 else None
    except ValueError:
        return None


def _parse_view_user(request: Request) -> str | None:
    """User de consultation (« voir en tant que », header `X-Oto-View-As` = sub cible).
    None = pas de header. Validé (opérateur + cible existe + GET) dans le middleware."""
    raw = request.headers.get("x-oto-view-as")
    if raw is None:
        return None
    return raw.strip() or None


# Ops de LECTURE des endpoints op-aware (POST `{op:…}`). Le dashboard LIT en POST
# (`{op:'list'}`, `{op:'get'}`, …) — une garde par méthode HTTP bloquerait donc les
# lectures. En consultation LECTURE SEULE (view-as user / inspection org opérateur),
# seules ces ops passent sur une requête non-GET ; toute autre op — ou un POST/PUT/
# DELETE sans op (= action/upload) — est une écriture, rejetée. Deny-by-default :
# élargir cette liste si une vraie lecture op-aware manque.
_READ_OPS = frozenset({
    "list", "get", "search", "revisions", "list_changes", "inventory",
    "list_templates", "preview", "describe", "status",
})


async def _peek_op(receive):
    """Bufferise le corps de la requête, en extrait le champ `op` (JSON), et rend un
    `receive` qui REJOUE le corps intact au handler aval. Les routes `/api/*` sont de
    petites requêtes JSON → bufferiser est sûr (`/mcp` streaming est exclu en amont).
    Retourne `(op | None, receive_rejoué)`."""
    messages: list = []
    while True:
        msg = await receive()
        messages.append(msg)
        if msg.get("type") != "http.request" or not msg.get("more_body", False):
            break
    body = b"".join(m.get("body", b"") for m in messages if m.get("type") == "http.request")
    op = None
    if body:
        try:
            data = json.loads(body)
            op = data.get("op") if isinstance(data, dict) else None
        except Exception:
            op = None
    i = 0

    async def replay():
        nonlocal i
        if i < len(messages):
            m = messages[i]
            i += 1
            return m
        return {"type": "http.request", "body": b"", "more_body": False}

    return op, replay


class ViewAsMiddleware:
    """Middleware ASGI **brut** (pas BaseHTTPMiddleware, qui bufferiserait le
    streaming `/mcp`) : n'intervient QUE sur `/api/*` portant `X-Oto-Org`, sinon
    pass-through total. Pose l'org de consultation (contextvar `session_org`) lue
    par le seam `access.current_org` → toute la résolution REST (autz + handlers +
    visibilité) scope la consultation, **sans** persister ni muter l'identité.

    Anti-IDOR : l'appartenance est validée ici (org>0) ; on ne fait JAMAIS confiance
    à l'en-tête. Sans header, ou non authentifié → la route suit son cours normal."""

    def __init__(self, app, verifier: JWTVerifier):
        self.app = app
        self._verifier = verifier

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope, receive)  # headers/query seulement → ne consomme pas le body
        view_org = _parse_view_org(request)
        view_group = _parse_view_group(request)
        view_user = _parse_view_user(request)
        if view_org is None and view_group is None and view_user is None:
            return await self.app(scope, receive, send)
        # sub RÉEL (apply_view_as=False) : sert à gater, jamais à appliquer la consultation.
        sub, err = await _authenticate(request, self._verifier, apply_view_as=False)
        if err:  # non authentifié → la route rendra son 401 ; pas de view-as
            return await self.app(scope, receive, send)
        from . import access, db, group_store, org_store, roles, session_org
        read_only = False  # consultation en LECTURE SEULE (view-as user OU inspection org opérateur)
        if view_user:  # « voir en tant que » : opérateur plateforme + cible existe + LECTURE SEULE
            if not await run_in_threadpool(access.is_platform_operator, sub):
                return await _json_error(request, 403, "forbidden")(scope, receive, send)
            if view_user == sub or await run_in_threadpool(db.get_user, view_user) is None:
                view_user = None  # cible = soi ou inconnue → pas de consultation (no-op)
            else:
                read_only = True
        if view_group:  # équipe consultée → valide la lecture + DÉRIVE son org parente (invariant)
            g = await run_in_threadpool(group_store.get_group, view_group)
            if g is None or not await run_in_threadpool(roles.can_read_group, sub, view_group):
                return await _json_error(request, 403, "forbidden")(scope, receive, send)
            view_org = g["org_id"]
        elif view_org:  # org>0 (0=perso = profil global, pas de check)
            # Membership RÉELLE (colonne DB, PAS l'escalade super_admin) : un membre
            # consulte son org normalement (lecture + écriture selon son rôle).
            real_role = await run_in_threadpool(org_store.get_org_role, view_org, sub)
            if real_role is not None:
                pass  # membre réel — comportement inchangé (writes gatés par le rôle)
            elif await run_in_threadpool(access.is_platform_operator, sub):
                # Opérateur plateforme NON-membre : inspection d'une org tierce en LECTURE
                # SEULE (même patron que le view-as user), même pour un super_admin (mode
                # inspection ≠ escalade d'admin).
                read_only = True
            else:
                return await _json_error(request, 403, "forbidden")(scope, receive, send)
        # Garde LECTURE SEULE : le dashboard LIT en POST op-aware (`{op:'list'|'get'}`),
        # donc on ne peut pas gater par méthode. Sur une requête non-GET, on lit l'`op`
        # du corps : seules les OPS DE LECTURE passent ; toute mutation (op d'écriture,
        # ou write sans op) → 403. Le corps est rejoué intact au handler.
        if read_only and request.method != "GET":
            op, receive = await _peek_op(receive)
            if op not in _READ_OPS:
                return await _json_error(request, 403, "view_as_read_only")(scope, receive, send)
        usr_token = session_org.set_view_user(view_user) if view_user is not None else None
        org_token = session_org.set_view_org(view_org) if view_org is not None else None
        grp_token = session_org.set_view_group(view_group) if view_group is not None else None
        try:
            return await self.app(scope, receive, send)
        finally:
            if grp_token is not None:
                session_org.reset_view_group(grp_token)
            if org_token is not None:
                session_org.reset_view_org(org_token)
            if usr_token is not None:
                session_org.reset_view_user(usr_token)


# --- Journalisation des appels REST dans le flux unifié (ADR 0017, kind='rest') ---
# La face MCP est tracée par otomata-calllog ; la face REST ne l'était PAS (3/4 de
# la plateforme invisibles au monitoring). Ce middleware comble le trou : une ligne
# tool_calls(kind='rest') par requête /api/*, dérivée du même substrat.

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_REST_LOG_TASKS: set = set()  # garde les refs des tâches fire-and-forget (anti-GC)


def _claimed_sub(request: Request) -> str | None:
    """Sub revendiqué par le bearer JWT, **NON vérifié** — attribution de log
    uniquement (jamais d'autz ; la route, elle, vérifie pour de vrai). Best-effort :
    token API opaque (`oto_…`) ou JWT malformé → None (ligne anonyme).

    Qualifié par tenant (ADR 0052) avec le MÊME qualificateur que le verifier : deux
    utilisateurs de deux émetteurs peuvent porter le même sub Logto, et sans ça leurs
    requêtes s'écriraient sur la même ligne d'audit — celle de l'utilisateur `oto`."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    parts = auth[7:].strip().split(".")
    if len(parts) != 3:  # pas un JWT → token opaque, pas d'attribution
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad))
        return tenancy.current().qualify_claims(claims)
    except Exception:
        return None


def _normalize_route(path: str) -> str:
    """Réduit la cardinalité pour l'agrégation : segments d'id (numériques / UUID)
    → `:id`. `/api/orgs/7/audit-log` → `/api/orgs/:id/audit-log`."""
    return "/".join(
        ":id" if (seg.isdigit() or _UUID_RE.match(seg)) else seg
        for seg in path.split("/")
    )


async def _emit_rest_event(row: dict) -> None:
    """Écrit l'événement hors event-loop (to_thread → insert sync non bloquant).
    Best-effort : une panne de log n'a jamais d'effet sur la requête servie."""
    try:
        await asyncio.to_thread(db.insert_tool_call, row)
    except Exception:  # noqa: BLE001 — le monitoring ne casse jamais le service
        logger.debug("rest call-log emit failed", exc_info=True)


class RestCallLogger:
    """Middleware ASGI **brut** : journalise chaque requête `/api/*` comme événement
    `kind='rest'` du flux unifié (ADR 0017). Pass-through total hors `/api/*` (ne
    touche JAMAIS le streaming `/mcp`) et sur les préflights `OPTIONS` (bruit CORS).
    `tool` = `MÉTHODE /route-normalisée` ; `ok` = 2xx/3xx ; les ≥400 portent le code
    dans `error`. Écriture en tâche de fond → zéro latence ajoutée, jamais bloquant."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        method = scope.get("method", "")
        if method == "OPTIONS":
            return await self.app(scope, receive, send)
        status = {"code": 0}

        async def _send(message):
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status", 0)
            await send(message)

        request = Request(scope, receive)  # headers/query only → ne consomme pas le body
        sub = _claimed_sub(request)
        org = _parse_view_org(request)  # org de consultation revendiquée (header), best-effort
        started = time.monotonic()
        try:
            await self.app(scope, receive, _send)
        finally:
            code = status["code"]
            row = {
                "kind": "rest",
                "tool": f"{method} {_normalize_route(scope.get('path', ''))}",
                "sub": sub,
                "org_id": org,
                "ok": 200 <= code < 400,
                "error": (f"HTTP {code}" if code >= 400 else None),
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
            task = asyncio.create_task(_emit_rest_event(row))
            _REST_LOG_TASKS.add(task)
            task.add_done_callback(_REST_LOG_TASKS.discard)


def make_routes(verifier: JWTVerifier, mcp_instance=None) -> Iterable:
    from starlette.routing import Route

    async def admin_platform_keys_list(request: Request) -> JSONResponse:
        sub, err = await _authenticate(request, verifier)
        if err:
            return err
        if not access.is_super_admin(sub):
            return _json_error(request, 403, "forbidden")
        # ADR 0044 §F : instances scope PLATFORM du coffre unifié (plus platform_keys). Le
        # secret n'est JAMAIS déchiffré/renvoyé — identité (provider, label, set_at) seulement.
        return _json(request, {"platform_keys": credentials_store.list_platform_credentials()})

    async def admin_platform_key_create(request: Request) -> JSONResponse:
        sub, err = await _authenticate(request, verifier)
        if err:
            return err
        if not access.is_super_admin(sub):
            return _json_error(request, 403, "forbidden")
        try:
            body = await request.json()
        except Exception:
            return _json_error(request, 400, "invalid_json")
        if not isinstance(body, dict):
            return _json_error(request, 400, "invalid_body")
        provider = (body.get("provider") or "").strip()
        label = (body.get("label") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        if provider not in db.KEY_PROVIDERS:
            return _json_error(request, 400, "invalid_provider")
        if not label or not api_key:
            return _json_error(request, 400, "missing_fields")
        # ADR 0044 §F : la clé plateforme est une instance scope PLATFORM du coffre unifié
        # (fin de platform_keys).
        try:
            credentials_store.set_credential(credentials_store.PLATFORM, label, provider,
                                             api_key, set_by=sub)
        except ValueError as e:
            return _json_error(request, 400, "invalid_platform_provider", str(e))
        return _json(request, {"provider": provider, "label": label})

    async def admin_platform_key_delete(request: Request) -> JSONResponse:
        sub, err = await _authenticate(request, verifier)
        if err:
            return err
        if not access.is_super_admin(sub):
            return _json_error(request, 403, "forbidden")
        provider = (request.path_params.get("provider") or "").strip()
        label = (request.path_params.get("label") or "").strip()
        # ADR 0044 §F : supprime l'instance plateforme (ses grants vivent sur sa ligne
        # share_down/meta → partent avec elle, pas d'orphelin).
        if not credentials_store.clear_credential(credentials_store.PLATFORM, label, provider):
            return _json_error(request, 404, "unknown_key")
        return _json(request, {"ok": True, "provider": provider, "label": label})

    # Gestion des jetons (palier admin) : `allow_api_token=False` — même règle que
    # `/api/me/tokens`, un jeton ne fabrique pas de jeton. Ici l'enjeu est pire :
    # ces routes émettent pour un sub TIERS.
    async def admin_tokens_list(request: Request) -> JSONResponse:
        sub, err = await _authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        if not access.is_super_admin(sub):
            return _json_error(request, 403, "forbidden")
        target_sub = request.path_params["sub"]
        if not db.get_user(target_sub):
            return _json_error(request, 404, "unknown_user")
        return _json(request, {"tokens": db.list_api_tokens(target_sub)})

    async def admin_tokens_create(request: Request) -> JSONResponse:
        sub, err = await _authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        if not access.is_super_admin(sub):
            return _json_error(request, 403, "forbidden")
        target_sub = request.path_params["sub"]
        if not db.get_user(target_sub):
            return _json_error(request, 404, "unknown_user")
        try:
            body = await request.json()
        except Exception:
            body = {}
        label = (body or {}).get("label") or "cli"
        ttl_raw = (body or {}).get("ttl_days")
        ttl_days = int(ttl_raw) if isinstance(ttl_raw, (int, str)) and str(ttl_raw).isdigit() else None
        try:
            scopes = token_scopes.parse((body or {}).get("scopes"))
        except token_scopes.ScopeError as e:
            return _json_error(request, 400, "invalid_scopes", str(e))
        token = db.create_api_token(target_sub, label=label.strip()[:32],
                                    ttl_days=ttl_days, scopes=scopes)
        return _json(request, {"token": token, "label": label, "ttl_days": ttl_days,
                               "scopes": scopes})

    async def admin_tokens_delete(request: Request) -> JSONResponse:
        sub, err = await _authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        if not access.is_super_admin(sub):
            return _json_error(request, 403, "forbidden")
        target_sub = request.path_params["sub"]
        try:
            token_id = int(request.path_params["token_id"])
        except ValueError:
            return _json_error(request, 400, "invalid_id")
        ok = db.delete_api_token(target_sub, token_id)
        if not ok:
            return _json_error(request, 404, "unknown_token")
        return _json(request, {"ok": True, "id": token_id})

    async def my_tools_list(request: Request) -> JSONResponse:
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

    async def my_tools_registry(request: Request) -> JSONResponse:
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

    async def my_tools_disable(request: Request) -> JSONResponse:
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

    async def my_tools_enable(request: Request) -> JSONResponse:
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

    async def _tool_by_name(name: str):
        """Objet Tool FastMCP par nom (ou None). `run_middleware=False` : hors
        session MCP (contexte REST) la chaîne de middleware n'a pas de Context."""
        if mcp_instance is None:
            return None
        tools = await mcp_instance.list_tools(run_middleware=False)
        for t in tools:
            if t.name == name:
                return t
        return None

    async def my_tool_detail(request: Request) -> JSONResponse:
        """Fiche d'un outil : description complète + schémas d'entrée/sortie
        (JSON Schema dérivé par FastMCP) + connecteur + état perso + testabilité.

        Alimente le panneau « en savoir plus » de la fiche connecteur (dashboard) —
        détail utile pour comprendre un outil (surtout open-data FOD) et, s'il est
        testable, générer un formulaire de test."""
        sub, err = await _authenticate(request, verifier)
        if err:
            return err
        name = request.path_params["name"]
        tool = await _tool_by_name(name)
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

    async def my_tool_call(request: Request) -> JSONResponse:
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
        tool = await _tool_by_name(name)
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

    datastore_routes = api_routes_datastore.make_routes(
        verifier=verifier,
        authenticate=_authenticate,
        json_response=_json,
        json_error=_json_error,
        cors_headers=_cors_headers,
        options_handler=options_handler,
    )

    sirene_routes = api_routes_sirene.make_routes(
        verifier=verifier,
        authenticate=_authenticate,
        json_response=_json,
        json_error=_json_error,
        options_handler=options_handler,
    )

    accords_routes = api_routes_accords.make_routes(
        verifier=verifier,
        authenticate=_authenticate,
        json_response=_json,
        json_error=_json_error,
        options_handler=options_handler,
    )

    atlassian_routes = api_routes_atlassian.make_routes(
        verifier=verifier,
        authenticate=_authenticate,
        json_response=_json,
        json_error=_json_error,
        options_handler=options_handler,
    )

    folk_routes = api_routes_folk.make_routes(
        verifier=verifier,
        authenticate=_authenticate,
        json_response=_json,
        json_error=_json_error,
        options_handler=options_handler,
    )

    # OAuth Zoho « server-based » — SECOND mode d'acquisition, le Self Client
    # restant intact et par défaut (les deux produisent le même credential).
    zoho_routes = api_routes_zoho.make_routes(
        verifier=verifier,
        authenticate=_authenticate,
        json_response=_json,
        json_error=_json_error,
        options_handler=options_handler,
    )

    salesforce_oauth_routes = api_routes_salesforce.make_routes(
        verifier=verifier,
        authenticate=_authenticate,
        json_response=_json,
        json_error=_json_error,
        options_handler=options_handler,
    )

    # Couche capacité (ADR 0009) : routes REST dérivées du registre (no-op tant
    # qu'il est vide — canari). Même séquence autz→validation→handler que MCP.
    capability_routes = _cap_rest_adapter.make_routes(
        verifier, _authenticate, _json, _json_error, options_handler,
        _cap_registry.CAPABILITIES,
    )

    # Cran d'activation des connecteurs (ADR 0010, B4) — admin only.
    connectors_routes = api_routes_connectors.make_routes(
        verifier, _authenticate, _json, _json_error, options_handler,
    )

    # Formulaire de contact public d'otomata.tech (non authentifié).
    contact_routes = api_routes_contact.make_routes(
        _json, _json_error, options_handler,
    )

    # Webhook Mollie (ADR 0043) — non authentifié, réconciliation événementielle.
    billing_webhook_routes = api_routes_billing.make_routes(options_handler)

    return [
        Route("/favicon.svg", public.favicon, methods=["GET"]),
        Route("/favicon.ico", public.favicon, methods=["GET"]),
        Route("/api/mcp/catalog", bind(public.mcp_catalog, mcp_instance=mcp_instance), methods=["GET"]),
        Route("/api/mcp/catalog", options_handler, methods=["OPTIONS"]),
        # Descriptif de l'API REST, dérivé (cf. openapi.py). Servi aux deux chemins
        # usuels : un intégrateur sonde l'un ou l'autre, aucun n'est plus canonique.
        Route("/openapi.json", public.openapi_doc, methods=["GET"]),
        Route("/openapi.json", options_handler, methods=["OPTIONS"]),
        Route("/api/openapi.json", public.openapi_doc, methods=["GET"]),
        Route("/api/openapi.json", options_handler, methods=["OPTIONS"]),
        Route("/api/connectors", bind(public.connectors_catalog, verifier=verifier), methods=["GET"]),
        Route("/api/connectors", options_handler, methods=["OPTIONS"]),
        Route("/api/doctrines/library", public.doctrines_library_public, methods=["GET"]),
        Route("/api/doctrines/library", options_handler, methods=["OPTIONS"]),
        Route("/api/doctrines/library/{slug}", public.doctrines_library_public_get, methods=["GET"]),
        Route("/api/doctrines/library/{slug}", options_handler, methods=["OPTIONS"]),
        Route("/api/guides/library", public.guides_library_public, methods=["GET"]),
        Route("/api/guides/library", options_handler, methods=["OPTIONS"]),
        Route("/api/guides/library/{slug}", public.guides_library_public_get, methods=["GET"]),
        Route("/api/guides/library/{slug}", options_handler, methods=["OPTIONS"]),
        Route("/api/invitations/code/{code}", public.invite_preview_by_code, methods=["GET"]),
        Route("/api/invitations/code/{code}", options_handler, methods=["OPTIONS"]),
        Route("/api/invitations/{token}", public.invite_preview, methods=["GET"]),
        Route("/api/invitations/{token}", options_handler, methods=["OPTIONS"]),
        Route("/api/me", bind(account.me, verifier=verifier), methods=["GET"]),
        Route("/api/me", options_handler, methods=["OPTIONS"]),
        Route("/api/me/avatar", bind(media.avatar_save, verifier=verifier), methods=["POST"]),
        Route("/api/me/avatar", bind(media.avatar_clear, verifier=verifier), methods=["DELETE"]),
        Route("/api/me/avatar", options_handler, methods=["OPTIONS"]),
        Route("/api/me/projects/{project_id:int}/files", bind(projects.project_files_list, verifier=verifier), methods=["GET"]),
        Route("/api/me/projects/{project_id:int}/files", bind(projects.project_files_upload, verifier=verifier), methods=["POST"]),
        Route("/api/me/projects/{project_id:int}/files", options_handler, methods=["OPTIONS"]),
        Route("/api/me/projects/{project_id:int}/files/{file_id:int}", bind(projects.project_file_delete, verifier=verifier), methods=["DELETE"]),
        Route("/api/me/projects/{project_id:int}/files/{file_id:int}", options_handler, methods=["OPTIONS"]),
        Route("/api/me/projects/{project_id:int}/files/{file_id:int}/public", bind(projects.project_file_public, verifier=verifier), methods=["POST"]),
        Route("/api/me/projects/{project_id:int}/files/{file_id:int}/public", options_handler, methods=["OPTIONS"]),
        Route("/api/public/docs/{token}", public.public_doc, methods=["GET"]),
        Route("/api/public/docs/{token}", options_handler, methods=["OPTIONS"]),
        # Réception d'un upload signé out-of-bande (#105) — jeton dans l'URL, pas de JWT.
        # PUT/POST = agent (curl brut) / formulaire humain (multipart) ; GET = page d'upload.
        Route("/api/upload/{token}", uploads.upload_receive, methods=["PUT", "POST"]),
        Route("/api/upload/{token}", uploads.upload_form, methods=["GET"]),
        Route("/api/upload/{token}", options_handler, methods=["OPTIONS"]),
        # Page de partage publique server-rendered (lisible par un agent, ADR gap
        # « pages SPA non lisibles »). Servie sous dashboard.oto.ninja via Caddy.
        Route("/p/d/{token}", public.public_doc_view, methods=["GET"]),
        Route("/api/orgs/{id}/logo", bind(media.org_logo_save, verifier=verifier), methods=["POST"]),
        Route("/api/orgs/{id}/logo", bind(media.org_logo_clear, verifier=verifier), methods=["DELETE"]),
        Route("/api/orgs/{id}/logo", options_handler, methods=["OPTIONS"]),
        Route("/api/me/calls", bind(account.my_calls, verifier=verifier), methods=["GET"]),
        Route("/api/me/calls", options_handler, methods=["OPTIONS"]),
        Route("/api/me/tools", my_tools_list, methods=["GET"]),
        Route("/api/me/tools", options_handler, methods=["OPTIONS"]),
        # `registry` AVANT `{name}` sinon Starlette le capture comme nom de tool.
        Route("/api/me/tools/registry", my_tools_registry, methods=["GET"]),
        Route("/api/me/tools/registry", options_handler, methods=["OPTIONS"]),
        Route("/api/me/tools/{name}", my_tools_disable, methods=["POST"]),
        Route("/api/me/tools/{name}", my_tools_enable, methods=["DELETE"]),
        Route("/api/me/tools/{name}", options_handler, methods=["OPTIONS"]),
        # Fiche + test d'un outil (dashboard) — suffixes distincts de `{name}` nu.
        Route("/api/me/tools/{name}/detail", my_tool_detail, methods=["GET"]),
        Route("/api/me/tools/{name}/detail", options_handler, methods=["OPTIONS"]),
        Route("/api/me/tools/{name}/call", my_tool_call, methods=["POST"]),
        Route("/api/me/tools/{name}/call", options_handler, methods=["OPTIONS"]),
        # /api/me/instructions* — migré en capacités (ADR 0009, capabilities/orgs_instructions.py),
        # monté par capability_routes plus bas.
        Route("/api/settings/api-keys/{provider}", bind(credentials.api_key_get, verifier=verifier), methods=["GET"]),
        Route("/api/settings/api-keys/{provider}", bind(credentials.api_key_save, verifier=verifier), methods=["POST"]),
        Route("/api/settings/api-keys/{provider}", bind(credentials.api_key_clear, verifier=verifier), methods=["DELETE"]),
        Route("/api/settings/api-keys/{provider}", options_handler, methods=["OPTIONS"]),
        # Connexion par session navigateur (brevo/crunchbase) — Live View depuis le dashboard.
        Route("/api/me/connectors/{name}/session/start", bind(credentials.session_start, verifier=verifier), methods=["POST"]),
        Route("/api/me/connectors/{name}/session/start", options_handler, methods=["OPTIONS"]),
        Route("/api/me/connectors/{name}/session/finalize", bind(credentials.session_finalize, verifier=verifier), methods=["POST"]),
        Route("/api/me/connectors/{name}/session/finalize", options_handler, methods=["OPTIONS"]),
        Route("/api/admin/platform-keys", admin_platform_keys_list, methods=["GET"]),
        Route("/api/admin/platform-keys", admin_platform_key_create, methods=["POST"]),
        Route("/api/admin/platform-keys", options_handler, methods=["OPTIONS"]),
        Route("/api/admin/platform-keys/{provider}/{label}", admin_platform_key_delete, methods=["DELETE"]),
        Route("/api/admin/platform-keys/{provider}/{label}", options_handler, methods=["OPTIONS"]),
        Route("/api/admin/users/{sub}/tokens", admin_tokens_list, methods=["GET"]),
        Route("/api/admin/users/{sub}/tokens", admin_tokens_create, methods=["POST"]),
        Route("/api/admin/users/{sub}/tokens", options_handler, methods=["OPTIONS"]),
        Route("/api/admin/users/{sub}/tokens/{token_id}", admin_tokens_delete, methods=["DELETE"]),
        Route("/api/admin/users/{sub}/tokens/{token_id}", options_handler, methods=["OPTIONS"]),
        Route("/api/me/activity-summary", bind(account.me_activity_summary, verifier=verifier), methods=["GET"]),
        Route("/api/me/activity-summary", options_handler, methods=["OPTIONS"]),
        Route("/api/me/projects/{id}/export", bind(projects.me_project_export, verifier=verifier), methods=["GET"]),
        Route("/api/me/projects/{id}/export", options_handler, methods=["OPTIONS"]),
        *datastore_routes,
        *sirene_routes,
        *accords_routes,
        *atlassian_routes,
        *folk_routes,
        *zoho_routes,
        *salesforce_oauth_routes,
        *capability_routes,
        *connectors_routes,
        *contact_routes,
        *billing_webhook_routes,
    ]
