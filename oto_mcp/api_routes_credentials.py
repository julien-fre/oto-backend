"""Handlers de la POSE d'un credential par le membre, et de la connexion par
session navigateur.

- `GET|POST|DELETE /api/settings/api-keys/{provider}` → le credential du membre
- `POST /api/me/connectors/{name}/session/{start,finalize}` → Live View Browserbase

Les deux voies produisent le même objet — un credential dans le coffre, scopé
`(sub, org)` (ADR 0033) — par deux gestes différents : un formulaire dérivé du
schéma du connecteur d'un côté, un login humain dans un navigateur hébergé de
l'autre. D'où le même fichier.

⚠️ Poser un secret est dashboard-only PAR DESIGN : jamais un argument MCP, il
transiterait par le contexte du LLM. Ce qui ne fait pas de ces routes une
« nature » — une capacité peut être REST-only (binding `mcp` retiré, cf.
`set_platform_key`) : c'est de la dette de migration, pas une exception.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api_routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

import asyncio

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import access, connectors, db
from .api_routes_base import _authenticate, _json, _json_error

# Saisie de credential per-user, GÉNÉRIQUE (modèle multi-champs, ADR 0011) :
# tout connecteur `byo_user` qui déclare un schéma de saisie (`secret_fields` :
# api_key 1 champ, basic_auth 2 champs, silae 3 champs…). Le formulaire, la
# validation et le packing dérivent du schéma — zéro branche par connecteur.
# cookie/oauth ont des flux dédiés (crunchbase/brevo via Live View Browserbase,
# google via OAuth) → `secret_fields` vide → exclus ici.

# Saisie de credential per-user, GÉNÉRIQUE (dérivée du registre, pas une liste
# hardcodée) : tout connecteur `byo_user` dont le secret est un "secret simple"
# — `api_key` (la clé) ou `basic_auth` (base64("email:password"), ex. planity).
# cookie/oauth ont des flows dédiés (crunchbase / google) → exclus ici.
_SETTABLE_KINDS = {"api_key", "basic_auth"}


def _credentialable(provider: str):
    c = connectors.connector_for_provider(provider)
    if c is None or not connectors.is_byo_user(provider) or not c.secret_fields:
        return None
    return c


async def api_key_save(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    provider = request.path_params["provider"]
    c = _credentialable(provider)
    if c is None:
        return _json_error(request, 404, "unknown_provider")
    # RBAC connecteur (ADR 0025) : aligner la POSE sur l'USAGE — un membre non
    # autorisé sur un connecteur RESTREINT dans son org ne peut pas poser de clé
    # perso (sinon une clé inerte serait posable en direct, hors UI). Même seam
    # que la résolution (`require_connector_access`), pas de règle dupliquée.
    from mcp.shared.exceptions import McpError
    try:
        access.require_connector_access(provider, sub)
    except McpError as e:
        return _json_error(request, 403, "connector_restricted", e.error.message)
    try:
        body = await request.json()
    except Exception:
        return _json_error(request, 400, "invalid_json")
    if not isinstance(body, dict):
        return _json_error(request, 400, "invalid_body")
    # Chaque champ `required` doit être non vide ; un champ facultatif
    # (connecteur « ET/OU » type slack) peut être omis, mais il faut au moins
    # un champ posé au total. Le packing (raw/base64/json) est encapsulé dans
    # credentials_store.pack_secret.
    from . import credentials_store
    fields: dict[str, str] = {}
    missing: list[str] = []
    for f in c.secret_fields:
        val = credentials_store.clean_field_value(f, body.get(f.name))
        if not val:
            if f.required:
                missing.append(f.label or f.name)
            continue
        fields[f.name] = val
    # NOMMER le champ manquant : un « missing_credentials » sec oblige à deviner
    # lequel des cinq champs bloque — vécu 28/07, un `data_center` vide a fait
    # échouer six tentatives de pose sans que rien ne le dise.
    if missing:
        return _json_error(request, 400, "missing_credentials",
                           "champ(s) requis vide(s) : " + ", ".join(missing))
    if not fields:
        return _json_error(request, 400, "missing_credentials",
                           "aucun champ renseigné.")
    db.upsert_user(sub)
    account = (body.get("account") or "").strip()
    # Scope MEMBRE (ADR 0033) : la clé est posée DANS l'org de contexte (org
    # consultée au dashboard via X-Oto-Org, sinon maison) — plus de credential
    # per-user org-agnostique. Poser en consultant movinmotion = scoper movinmotion.
    org_id = access.current_org(sub)
    if org_id is None:
        return _json_error(request, 400, "no_org_context")
    eid = credentials_store.member_id(org_id, sub)
    # Garde de pose (source unique, #409) : multi-compte → cohérence des comptes
    # de CE membre ('' et comptes nommés ne coexistent pas, sinon la
    # désambiguïsation à la résolution voit un '' impossible à désigner ; au 1er
    # compte NOMMÉ la ligne '' migre vers « principal »). Mono-compte → un compte
    # nommé est REFUSÉ, jamais écrit puis ignoré.
    try:
        credentials_store.guard_account_write(
            credentials_store.MEMBER, eid, provider, account)
    except credentials_store.NamedAccountRequired as e:
        return _json_error(request, 409, "account_required", str(e))
    except credentials_store.SingleAccountConnector as e:
        return _json_error(request, 400, "single_account_connector", str(e))
    # Connexion en DEUX temps : le formulaire ne collecte que les PRÉREQUIS, le
    # champ décisif (refresh_token) arrive par le consentement. Sans reprise, une
    # simple correction de champ après connexion repackerait un blob SANS lui —
    # l'UI dirait « enregistré » et le connecteur casserait au 1er appel d'outil.
    # Gardé sur la MÊME source unique que la sonde ci-dessous (`status_hints`) :
    # un connecteur qui déclare un état déclare, de fait, que son credential se
    # complète hors formulaire.
    from . import status_hints
    if status_hints.credential_state(provider, fields) is not None:
        declared = {f.name for f in c.secret_fields}
        prior = credentials_store.get_credential_with_meta(
            credentials_store.MEMBER, eid, provider, account=account) or {}
        if prior.get("secret"):
            fields = {**{k: v for k, v in
                         credentials_store.unpack_secret(provider, prior["secret"]).items()
                         if k not in declared and v},
                      **fields}
    # Verify-avant-persist (#106) : si le connecteur expose une sonde, on TESTE la
    # connexion avec les champs candidats AVANT d'écrire — un credential qui
    # n'authentifie pas n'est jamais persisté (l'erreur remonte à la SAISIE, pas au
    # 1er appel d'outil, plus tard et hors contexte). `config` vide : les
    # connecteurs de ce chemin (zoho/brevo…) portent tout dans `fields` ; le dsn
    # unipile passe par un flux dédié, pas api_key_save. Sans sonde → pose directe.
    from . import connector_verify, status_hints
    verified = False
    # ⚠️ Connexion en DEUX temps : un credential VOLONTAIREMENT incomplet (app
    # OAuth posée, consentement à venir) échoue la sonde PAR CONSTRUCTION. Le
    # refuser ici crée un blocage circulaire — on ne peut pas poser l'app, donc
    # jamais consentir, donc jamais compléter le credential. Vécu 28/07 : six
    # tentatives de pose Zoho, toutes rejetées, sans chemin de sortie.
    # L'état déclaré (`status_hints`, source unique) dit si l'incomplétude est
    # ATTENDUE ; dans ce cas on saute la sonde et on persiste — l'étape suivante
    # est portée par le verdict de la fiche.
    st = status_hints.credential_state(provider, fields)
    pending = st is not None and not st.complete
    if connector_verify.supports(provider) and not pending:
        try:
            await connector_verify.run(provider, fields)
        except McpError as e:
            return _json_error(request, 400, "verify_failed", e.error.message)
        except Exception as e:  # noqa: BLE001 — l'échec d'auth EST le résultat
            return _json_error(request, 400, "verify_failed", str(e))
        verified = True
    secret = credentials_store.pack_secret(provider, fields)
    meta = None
    if verified:
        from datetime import datetime, timezone
        meta = {"verified_at": datetime.now(timezone.utc).isoformat()}
    credentials_store.set_credential(
        credentials_store.MEMBER, eid, provider, secret, set_by=sub,
        account=account, meta=meta)
    return _json(request, {"ok": True, "provider": provider, "org_id": org_id,
                           "account": account, "verified": verified,
                           # `pending_action` = ce credential est enregistré mais
                           # demande une étape de plus (consentement OAuth…).
                           "pending_action": st.next_action if pending else None})


async def api_key_clear(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    provider = request.path_params["provider"]
    # Effacer est générique : tout connecteur byo_user (clé multi-champs OU
    # session navigateur sans champ, ex. brevo/crunchbase). On ne dépend PAS de
    # `secret_fields` comme GET/SAVE — sinon la déconnexion d'une session
    # Browserbase 404 (route `/api/settings/crunchbase` retirée par ADR 0026).
    c = connectors.connector_for_provider(provider)
    if c is None or not connectors.is_byo_user(provider):
        return _json_error(request, 404, "unknown_provider")
    from . import credentials_store, roles
    org_id = access.current_org(sub)
    if org_id is None:
        return _json_error(request, 400, "no_org_context")
    # Scope de la déconnexion (miroir de la pose) : member (défaut), org, group.
    # Effacer un secret partagé exige d'être admin du scope.
    scope = (request.query_params.get("scope") or "member").strip()
    # Multi-compte : `?account=` cible un compte précis ('' = mono legacy) —
    # à chaque palier depuis la Phase 2 (2026-08-25).
    account = (request.query_params.get("account") or "").strip()
    if scope == "org":
        if not roles.is_org_admin(sub, org_id):
            return _json_error(request, 403, "forbidden")
        credentials_store.clear_credential(credentials_store.ORG, str(org_id), provider,
                                           account=account)
        return _json(request, {"ok": True, "provider": provider, "account": account, "scope": scope})
    if scope == "group":
        group_id = access.current_group(sub)
        if group_id is None:
            return _json_error(request, 400, "no_group_context")
        if not roles.can_admin_group(sub, group_id):
            return _json_error(request, 403, "forbidden")
        credentials_store.clear_credential("group", str(group_id), provider, account=account)
        return _json(request, {"ok": True, "provider": provider, "account": account, "scope": scope})
    credentials_store.clear_credential(
        credentials_store.MEMBER, credentials_store.member_id(org_id, sub), provider,
        account=account)
    return _json(request, {"ok": True, "provider": provider, "account": account, "scope": "member"})


# --- Connexion par session navigateur (brevo, crunchbase) — la VOIE PRODUIT :
# le bouton « Connecter » du dashboard ouvre une Live View Browserbase en iframe,
# l'utilisateur se logue, puis « finalize » vérifie + persiste le Context. Même
# corps de logique que les tools MCP `<name>_connect_start/_status` (seam partagé
# `browser_session`). `start` est BLOQUANT (HTTP Browserbase) → `to_thread`.
async def session_start(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from . import browser_session
    name = request.path_params["name"]
    if not browser_session.is_session_connector(name):
        return _json_error(request, 404, "not_a_session_connector")
    # Connecteur GÉNÉRIQUE (`browser`, oto-private#79) : le SITE vient de l'appel —
    # `?url=` ouvre la Live View sur la page de connexion demandée. Absent (les
    # connecteurs à site unique) ⇒ la `login_url` enregistrée, comportement inchangé.
    url = (request.query_params.get("url") or "").strip() or None
    try:
        out = await asyncio.to_thread(
            lambda: browser_session.start(sub, name, login_url=url))
    except browser_session.SessionError as e:
        return _json_error(request, 503, "browserbase_unavailable", str(e))
    return _json(request, out)


async def session_finalize(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    from . import browser_session
    name = request.path_params["name"]
    if not browser_session.is_session_connector(name):
        return _json_error(request, 404, "not_a_session_connector")
    try:
        body = await request.json()
    except Exception:
        return _json_error(request, 400, "invalid_json")
    context_id = (body or {}).get("context_id")
    session_id = (body or {}).get("session_id")
    if not context_id or not session_id:
        return _json_error(request, 400, "missing_params")
    # Niveau de configuration de l'instance (ADR 0038/0044) : member (défaut, ma
    # session perso), org (partagée à toute l'org), group (partagée à l'équipe).
    # Les niveaux partagés exigent d'être admin du scope + connecteur org-partageable.
    scope = ((body or {}).get("scope") or "member").strip()
    if scope not in ("member", "org", "group"):
        return _json_error(request, 400, "invalid_scope")
    from . import roles
    group_id = None
    if scope in ("org", "group"):
        org_id = access.current_org(sub)
        if org_id is None:
            return _json_error(request, 400, "no_org_context")
        if not connectors.is_org_shareable(name):
            return _json_error(request, 400, "not_org_shareable")
        if scope == "org":
            if not roles.is_org_admin(sub, org_id):
                return _json_error(request, 403, "forbidden")
        else:
            group_id = access.current_group(sub)
            if group_id is None:
                return _json_error(request, 400, "no_group_context")
            if not roles.can_admin_group(sub, group_id):
                return _json_error(request, 403, "forbidden")
    # Compte du coffre visé — connecteur générique : le site (host). `force` =
    # persister sans la vérification générique de login (refusé par le seam pour
    # un connecteur à site unique, dont le verify est une vraie sonde d'API).
    account = ((body or {}).get("account") or "").strip()
    force = bool((body or {}).get("force"))
    try:
        connected = await browser_session.finalize(
            sub, name, context_id, session_id, scope=scope, group_id=group_id,
            account=account, force=force)
    except browser_session.SessionError as e:
        return _json_error(request, 502, "session_verify_failed", str(e))
    return _json(request, {"connected": connected, "scope": scope,
                           "account": account})


async def api_key_get(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    provider = request.path_params["provider"]
    c = _credentialable(provider)
    if c is None:
        return _json_error(request, 404, "unknown_provider")
    from . import credentials_store
    org_id = access.current_org(sub)
    secret = (credentials_store.get_credential(
                  credentials_store.MEMBER,
                  credentials_store.member_id(org_id, sub), provider)
              if org_id is not None else None)
    if not secret:
        return _json_error(request, 404, "not_configured")
    # GÉNÉRIQUE : on dépack et on ne renvoie que les champs `reveal` (l'api_key,
    # pour copier) ou non-`secret` (l'email). Jamais un mot de passe / secret.
    fields = credentials_store.unpack_secret(provider, secret)
    out: dict = {"provider": provider, "configured": True}
    for f in c.secret_fields:
        if f.reveal or not f.secret:
            out[f.name] = fields.get(f.name)
    return _json(request, out)
