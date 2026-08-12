"""Routes REST datastore + Google OAuth + API tokens.

Extrait de `api_routes.py` pour respecter la limite 500 LOC.

Endpoints exposés :

- `GET    /api/google/oauth/start`              → renvoie {auth_url}
- `GET    /api/google/oauth/callback`           → no auth (Google redirige)
- `GET    /api/google/oauth/status`             → {connected, granted_at, scopes}
- `DELETE /api/google/oauth`                    → révoque

- `GET    /api/me/tokens`                       → liste tokens CLI (sans plaintext)
- `POST   /api/me/tokens`                       → crée un token, renvoie le plaintext (one-shot)
- `DELETE /api/me/tokens/{token_id}`            → révoque

- `PUT    /api/datastore/namespaces/{ns}/schema` → pose/retire le schéma typé
- `GET|POST|DELETE /api/datastore/namespaces/{ns}/share` → partages nominatifs

Auth : Bearer JWT Logto **ou** API token long-lived (préfixe `oto_`),
résolu via `_authenticate` (partagé avec `api_routes.py`).

⚠️ **Org ciblée = en-tête `X-Oto-Org: <org_id>`** (pas un query param `?org=`, qui
est ignoré). Sans lui, tout est résolu sur l'org ACTIVE du porteur du token — donc
le tableau d'une autre de ses organisations est invisible, et répond 404. L'en-tête
est validé pour appartenance (`ViewAsMiddleware`) et déjà déclaré en CORS, donc
utilisable depuis un navigateur. Un 404 sur un namespace qui existe ailleurs le
rappelle désormais dans son `detail` (signal #316).

**Réserver une ligne vit à côté** (signal #362) : `POST …/claim_next` et
`POST …/rows/{row_id}/claim` sont des CAPACITÉS (`capabilities/datastore_claim.py`),
pas des routes écrites ici — une surface neuve naît capacité. Seule la LIBÉRATION
reste dans ce module, avec les autres routes datastore historiques.
"""
from __future__ import annotations

import json
import os
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import access, datastore_journal, db, google_oauth, org_store, ownership, roles, token_scopes
from .datastore import (
    NamespaceExists,
    NamespaceForbidden,
    NamespaceNotFound,
    NamespaceReadOnly,
    RowNotFound,
    RowValidationError,
    make_store,
)


# Type alias for the auth helper passed in from api_routes.
AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def _app_url() -> str:
    return os.environ.get("OTO_APP_URL", "https://app.oto.ninja").rstrip("/")


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    cors_headers: Callable[[str | None], dict[str, str]],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:
    """Construit les routes datastore.

    Les helpers `authenticate`/`json_response`/`json_error`/`cors_headers`/
    `options_handler` sont passés depuis `api_routes.py` pour partager les
    primitives (auth Logto + token, CORS).
    """

    def _ns_not_found(request: Request, sub: str, namespace: str) -> JSONResponse:
        """404 qui dit OÙ vit le tableau quand il appartient à une autre org du user.

        L'API résout le store sur l'org ACTIVE ; viser le tableau d'une autre org
        demande l'en-tête `X-Oto-Org`, qui n'apparaissait ni dans la description des
        routes ni dans le moindre message. Un namespace bien réel répondait donc
        « namespace_not_found », ce qui se lit comme « il n'existe pas » — temps
        perdu, et un faux diagnostic produit au passage (signal #316).

        On ne nomme que des orgs dont le porteur du token est MEMBRE : l'indice ne
        révèle rien qu'il ne puisse déjà lister. Fail-open : au moindre pépin, le
        404 nu d'avant."""
        try:
            orgs = {int(o["org_id"]): o.get("name") for o in org_store.list_orgs_for_user(sub)}
            owners = [("org", str(i)) for i in orgs]
            elsewhere = [n for n in db.list_datastore_namespaces_for_owners(owners)
                         if n["namespace"] == namespace]
            if elsewhere:
                where = ", ".join(
                    f"{orgs.get(int(n['owner_id'])) or 'org'} (org {n['owner_id']})"
                    for n in elsewhere)
                first = elsewhere[0]["owner_id"]
                return json_error(
                    request, 404, "namespace_not_found",
                    f"« {namespace} » existe, mais dans une autre de tes organisations : "
                    f"{where}. Rejoue la requête avec l'en-tête « X-Oto-Org: {first} ».")
        except Exception:  # noqa: BLE001 — un indice ne doit jamais casser la réponse
            pass
        return json_error(request, 404, "namespace_not_found")

    # --- Google OAuth ----------------------------------------------------

    async def google_oauth_start(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        try:
            url = google_oauth.build_auth_url(sub)
        except RuntimeError as e:
            return json_error(request, 500, f"oauth_misconfigured: {e}")
        return json_response(request, {"auth_url": url})

    async def google_oauth_callback(request: Request) -> Response:
        # Pas d'auth Logto — Google redirige depuis le navigateur user.
        # Validation via le `state` HMAC-signé.
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return json_error(request, 400, "missing_code_or_state")
        parsed = google_oauth.verify_state(state)
        if not parsed:
            return json_error(request, 400, "invalid_state")
        sub, org_id = parsed
        try:
            tokens = google_oauth.exchange_code(code)
            google_oauth.persist_token(sub, org_id, tokens)
        except Exception as e:
            return json_error(request, 502, f"oauth_exchange_failed: {e}")
        # Retour vers la page connecteurs (où vit la config Google, ADR 0024 B2).
        # `datastore` n'est plus Google Sheets (ADR 0016, PG natif) → ex-signal
        # `?datastore=connected` retiré.
        return RedirectResponse(url=f"{_app_url()}/console/connectors?google=connected", status_code=302)

    async def google_oauth_status(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        accounts = google_oauth.list_accounts(sub)
        default = next((a for a in accounts if a.get("is_default")), None)
        return json_response(request, {
            "connected": bool(accounts),
            # Compat : champs au niveau racine = compte par défaut.
            "granted_at": default["granted_at"] if default else None,
            "scopes": default["scopes"].split() if default and default.get("scopes") else [],
            "accounts": [
                {
                    "email": a.get("google_email"),
                    "is_default": a.get("is_default", False),
                    "scopes": a["scopes"].split() if a.get("scopes") else [],
                    "granted_at": a.get("granted_at"),
                }
                for a in accounts
            ],
        })

    async def google_oauth_revoke(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        # ?account=<email> révoque un compte précis ; absent = tous.
        account = request.query_params.get("account") or None
        google_oauth.revoke(sub, account=account)
        return json_response(request, {"ok": True, "account": account})

    async def google_oauth_set_default(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        account = (body.get("account") if isinstance(body, dict) else None) or ""
        account = account.strip()
        if not account:
            return json_error(request, 400, "missing_account")
        org_id = access.current_org(sub)
        if org_id is None or not db.set_default_google_account(sub, org_id, account):
            return json_error(request, 404, "unknown_account")
        return json_response(request, {"ok": True, "default": account})

    # --- API tokens (CLI auth) -------------------------------------------

    # --- Jetons API : gestion réservée à une SESSION INTERACTIVE ---------------
    # `allow_api_token=False` sur les trois : un jeton `oto_` ne peut ni lister, ni
    # créer, ni révoquer de jeton. Sinon une fuite est auto-entretenue (l'attaquant
    # s'émet un second jeton non-expirant avant qu'on révoque le premier) et peut
    # révoquer les jetons légitimes. Émettre un jeton reste donc un acte humain,
    # ce qui est exactement ce qu'on veut d'un jeton confié à un tiers.

    async def me_tokens_list(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        return json_response(request, {"tokens": db.list_api_tokens(sub)})

    async def me_tokens_create(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        label = body.get("label") or "cli"
        # Portée optionnelle (`token_scopes`) : absente ⇒ jeton non porté (il EST le
        # sub). Présente ⇒ jeton borné à des tableaux nommés — la forme à confier à
        # une intégration tierce. Validée ici, jamais côté porteur.
        try:
            scopes = token_scopes.parse(body.get("scopes"))
        except token_scopes.ScopeError as e:
            return json_error(request, 400, "invalid_scopes", str(e))
        if scopes is not None:
            # Refuser un tableau que l'émetteur ne voit pas : le jeton ne peut de
            # toute façon pas dépasser les droits du sub, mais une faute de frappe
            # produirait un jeton muet qu'on croirait branché.
            visible = {n["namespace"] for n in make_store(sub).list_namespaces()}
            missing = sorted(set(scopes["namespaces"]) - visible)
            if missing:
                return json_error(request, 400, "unknown_namespace",
                                  f"Tableaux inconnus dans l'org active : {missing}")
        token = db.create_api_token(sub, label=label.strip()[:32], scopes=scopes)
        return json_response(request, {"token": token, "label": label, "scopes": scopes},
                             status=201)

    async def me_tokens_delete(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        try:
            token_id = int(request.path_params["token_id"])
        except (ValueError, KeyError):
            return json_error(request, 400, "invalid_id")
        ok = db.delete_api_token(sub, token_id)
        if not ok:
            return json_error(request, 404, "unknown_token")
        return json_response(request, {"ok": True})

    # --- Datastore (PG natif, ADR 0016) ----------------------------------

    # Lister / créer / supprimer / renommer un tableau, et son deep-link, sont des
    # CAPACITÉS (`capabilities/datastore_namespaces.py`, #302) : mêmes chemins, mêmes
    # réponses, mais entrée ET sortie déclarées — donc décrites dans
    # `/api/openapi.json`, donc générables chez un intégrateur.

    # Les LIGNES sont des CAPACITÉS (`capabilities/datastore_rows.py`, #302) : page,
    # fiche, ajout, modification, suppression, file de travail et agrégat. Mêmes
    # chemins, mêmes réponses, mais entrée ET sortie déclarées — les deux corps LIBRES
    # (ajouter/modifier : les colonnes du tableau) le sont explicitement, par
    # `RestBinding.body_field`.

    async def ds_set_schema(request: Request) -> JSONResponse:
        """Pose/retire le schéma typé d'un namespace (ADR 0032 §6 / 0029, B6).
        Corps : {schema: {fields:[...]}} ou {schema: null} pour repasser en table libre."""
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return json_error(request, 400, "invalid_json")
        if not isinstance(body, dict):
            return json_error(request, 400, "invalid_body")
        namespace = request.path_params["namespace"]
        try:
            return json_response(request, make_store(sub).set_schema(namespace, body.get("schema")))
        except NamespaceNotFound:
            return _ns_not_found(request, sub, namespace)
        except NamespaceReadOnly:
            return json_error(request, 403, "namespace_read_only")
        except ValueError:
            return json_error(request, 400, "invalid_schema")

    def _govern_ns(sub: str, namespace: str) -> tuple[int | None, tuple[int, str] | None]:
        """Résout le namespace par nom + vérifie le droit de GOUVERNANCE de l'acteur
        (owner ∪ escalade roles.py). Retourne (ns_id, None) ou (None, (status, code))."""
        try:
            ns_id = make_store(sub).resolve_ns_id(namespace)
        except NamespaceNotFound:
            return None, (404, "namespace_not_found")
        if not ownership.can_govern(sub, "datastore_namespace", str(ns_id)):
            return None, (403, "forbidden")
        return ns_id, None

    async def ds_share(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        namespace = request.path_params["namespace"]
        try:
            body = await request.json()
        except Exception:
            return json_error(request, 400, "invalid_json")
        email = (body.get("email") or "").strip()
        permission = (body.get("permission") or "write").strip()
        if not email:
            return json_error(request, 400, "email_required")
        if permission not in ("read", "write"):
            return json_error(request, 400, "permission must be 'read' or 'write'")
        recipient = db.get_user_by_email(email)
        if not recipient:
            return json_error(request, 404, f"no oto user with email {email}")
        ns_id, gerr = _govern_ns(sub, namespace)
        if gerr:
            return json_error(request, gerr[0], gerr[1])
        ownership.grant("datastore_namespace", str(ns_id), "user", recipient["sub"],
                        permission, granted_by=sub)
        return json_response(
            request,
            {"ok": True, "namespace": namespace, "shared_with": email, "permission": permission},
        )

    async def ds_unshare(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        namespace = request.path_params["namespace"]
        try:
            body = await request.json()
        except Exception:
            return json_error(request, 400, "invalid_json")
        email = (body.get("email") or "").strip()
        if not email:
            return json_error(request, 400, "email_required")
        recipient = db.get_user_by_email(email)
        if not recipient:
            return json_error(request, 404, f"no oto user with email {email}")
        ns_id, gerr = _govern_ns(sub, namespace)
        if gerr:
            return json_error(request, gerr[0], gerr[1])
        removed = ownership.revoke("datastore_namespace", str(ns_id), "user", recipient["sub"])
        if not removed:
            return json_error(request, 404, f"no active share for {email} on {namespace}")
        return json_response(request, {"ok": True, "namespace": namespace, "removed": email})

    async def ds_list_shares(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        namespace = request.path_params["namespace"]
        ns_id, gerr = _govern_ns(sub, namespace)
        if gerr:
            return json_error(request, gerr[0], gerr[1])
        shares = [
            {"email": s.get("email"), "permission": s.get("permission"),
             "principal_type": s.get("principal_type"), "principal_id": s.get("principal_id"),
             "created_at": s.get("granted_at")}
            for s in ownership.list_grants("datastore_namespace", str(ns_id))
        ]
        return json_response(request, {"shares": shares})

    # Le TRANSFERT de propriété d'un datastore passe par la capacité UNIQUE `oto_resource`
    # (op=transfer, resource_type='datastore_namespace' — même seam `ownership` + garde-fou
    # anti-lockout + cibles user/org/GROUPE). L'ancien endpoint bespoke `ds_transfer` a été
    # retiré (2026-07-24) : il dupliquait la résolution de cible et court-circuitait la
    # confirmation de perte de contrôle. Le front vise `/api/resources` par l'id du namespace.

    return [
        # Google OAuth
        Route("/api/google/oauth/start", google_oauth_start, methods=["GET"]),
        Route("/api/google/oauth/start", options_handler, methods=["OPTIONS"]),
        Route("/api/google/oauth/callback", google_oauth_callback, methods=["GET"]),
        Route("/api/google/oauth/status", google_oauth_status, methods=["GET"]),
        Route("/api/google/oauth/status", options_handler, methods=["OPTIONS"]),
        Route("/api/google/oauth", google_oauth_revoke, methods=["DELETE"]),
        Route("/api/google/oauth/default", google_oauth_set_default, methods=["POST"]),
        Route("/api/google/oauth/default", options_handler, methods=["OPTIONS"]),
        Route("/api/google/oauth", options_handler, methods=["OPTIONS"]),
        # API tokens
        Route("/api/me/tokens", me_tokens_list, methods=["GET"]),
        Route("/api/me/tokens", me_tokens_create, methods=["POST"]),
        Route("/api/me/tokens", options_handler, methods=["OPTIONS"]),
        Route("/api/me/tokens/{token_id}", me_tokens_delete, methods=["DELETE"]),
        Route("/api/me/tokens/{token_id}", options_handler, methods=["OPTIONS"]),
        # Datastore
        Route("/api/datastore/namespaces/{namespace}/schema", ds_set_schema, methods=["PUT"]),
        Route("/api/datastore/namespaces/{namespace}/schema", options_handler, methods=["OPTIONS"]),
        Route("/api/datastore/namespaces/{namespace}/share", ds_list_shares, methods=["GET"]),
        Route("/api/datastore/namespaces/{namespace}/share", ds_share, methods=["POST"]),
        Route("/api/datastore/namespaces/{namespace}/share", ds_unshare, methods=["DELETE"]),
        Route("/api/datastore/namespaces/{namespace}/share", options_handler, methods=["OPTIONS"]),
    ]
