"""Microsoft 365 — la connexion d'une PERSONNE (OAuth 2.0, permissions déléguées).

Flux hébergé par oto, sur le patron commun (`connectors/flow` + `auth/flow`), même
forme que `meta_ads` :

1. « Se connecter » sur la fiche → `_start_flow` rend l'URL du dialogue Microsoft,
   avec un `state` signé qui porte l'identité ;
2. Microsoft ramène le navigateur sur `/api/microsoft/oauth/callback`
   (`api/microsoft.py`) ; le code y devient un refresh token ;
3. le refresh token part au coffre, palier MEMBRE : la personne agit avec SES
   droits Microsoft 365, ni plus ni moins.

**Renouvellement.** Un jeton d'accès vit une heure ; `access_token_for` le
redemande avec le refresh token, et ⚠️ Entra fait TOURNER ce dernier : le nouveau
remplace l'ancien au coffre à chaque renouvellement. Le jeton d'accès lui-même ne
va jamais en base : il vit dans un cache du process, keyé par le hash du refresh
token (une reconnexion l'invalide d'elle-même).

⚠️ Les coordonnées de l'application Microsoft d'oto (multilocataire, enregistrée
dans l'annuaire de l'exploitant) vivent en base, scope PLATEFORME de
`connector_settings`, posées par l'exploitant.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .. import credentials_store, status_hints
from ..connectors import flow as connector_flow
from ..connectors import health as connector_health
from ..connectors import link as connector_link
from . import flow as oauth_flow

logger = logging.getLogger("oto_mcp.auth.microsoft")

CONNECTOR = "sharepoint"

_AUD = "microsoft"
_CALLBACK_PATH = "/api/microsoft/oauth/callback"

#: `client_secret` porte le suffixe `_secret` que la console admin masque.
_REGLAGES = ("client_id", "client_secret")

_COMMANDE = ('oto_admin_connector_setting(op="set", connector="sharepoint", '
             'key="<key>", value="<value>")')

# {clé opaque: (access_token, échéance epoch)} — jamais en base.
_JETONS: dict[str, tuple[str, float]] = {}
_VERROU = threading.Lock()


class MicrosoftReauthRequired(RuntimeError):
    """L'autorisation de la personne est morte : elle doit se reconnecter."""


def _coeur():
    """`oto.tools.microsoft` d'oto-core, importé À L'APPEL — le connecteur reste
    monté (et refuse en le disant) quand le tag épinglé ne le porte pas encore."""
    import importlib

    try:
        return importlib.import_module("oto.tools.microsoft")
    except ImportError as e:
        raise RuntimeError(
            "The `sharepoint` connector is not installed on this instance: its core "
            "lives in oto-core and the pinned tag does not ship it yet. This is an "
            "instance configuration issue, not your account — tell the operator. "
            f"Detail: {e}") from e


# --- les coordonnées de l'application ----------------------------------------

def _reglages() -> dict:
    """Lecture en base : au démarrage d'un flux, au retour d'un consentement, et
    au RENOUVELLEMENT d'un jeton d'accès (une fois par heure et par personne)."""
    from ..db import connector_settings as store

    return {r["key"]: (r["value"] or "").strip()
            for r in store.list_connector_settings()
            if r["scope_type"] == "platform" and r["connector"] == CONNECTOR
            and r["key"] in _REGLAGES}


def coordonnees_manquantes() -> list[str]:
    poses = _reglages()
    return [nom for nom in _REGLAGES if not poses.get(nom)]


def app() -> dict:
    """`{client_id, client_secret}` de l'instance, ou un refus qui NOMME ce qui manque."""
    manquantes = coordonnees_manquantes()
    if manquantes:
        raise RuntimeError(
            f"The `sharepoint` connector is not configured on this instance: missing "
            f"{', '.join(manquantes)}. Nothing to do on your account — the instance "
            f"operator sets them with {_COMMANDE}.")
    poses = _reglages()
    return {"client_id": poses["client_id"], "client_secret": poses["client_secret"]}


def app_disponible(sub: str) -> bool:
    del sub
    return not coordonnees_manquantes()


# --- le state signé ----------------------------------------------------------

def _ctx_org(sub: str) -> int:
    from .. import access  # lazy : évite tout cycle d'import au boot

    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "No org in context — cannot scope the Microsoft connection. Sign in "
            "again and retry.")
    return org


def make_state(sub: str, org_id: int, return_app: str = "") -> str:
    return oauth_flow.sign_state(_AUD, {"sub": sub, "org": org_id, "app": return_app})


def verify_state(state: str) -> Optional[tuple[str, int, str]]:
    """`(sub, org_id, return_app)` si le state est valide et émis POUR ce flux."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    sub, org, return_app = data.get("sub"), data.get("org"), data.get("app")
    if not isinstance(sub, str) or not isinstance(org, int):
        return None
    return sub, org, return_app if isinstance(return_app, str) else ""


# --- démarrage du flux -------------------------------------------------------

def build_auth_url(sub: str, return_app: str = "") -> str:
    org_id = _ctx_org(sub)
    return _coeur().auth.authorize_url(
        app()["client_id"], oauth_flow.redirect_uri(_CALLBACK_PATH),
        make_state(sub, org_id, oauth_flow.resolve_return_app(return_app)))


def _start_flow(ctx, values: dict) -> "connector_flow.FlowStart":
    from ..capabilities._types import AuthzDenied

    try:
        return connector_flow.FlowStart(
            auth_url=build_auth_url(ctx.sub, (values or {}).get("app") or ""))
    except RuntimeError as e:
        raise AuthzDenied(503, "oauth_misconfigured", str(e))


connector_flow.declare(
    CONNECTOR,
    start=_start_flow,
    label="Sign in with Microsoft",
    callback_path=_CALLBACK_PATH,
    app_ready=app_disponible,
)


# --- le coffre ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope(org_id: int, sub: str) -> tuple[str, str]:
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


def _cle(entity_id: str, refresh_token: str) -> str:
    """Clé du cache : la ligne ET son refresh token, hachés (jamais un secret en
    clair comme clé). Une reconnexion change le refresh token, donc la clé."""
    return hashlib.sha256(f"{entity_id}|{refresh_token}".encode()).hexdigest()


def _garder(entity_id: str, grant) -> None:
    with _VERROU:
        _JETONS[_cle(entity_id, grant.refresh_token)] = (
            grant.access_token, time.time() + int(grant.expires_in))


def persist_grant(sub: str, org_id: int, grant) -> dict:
    """Le refresh token est le secret (`secret_kind="oauth"`) ; l'identité du
    compte Microsoft va dans `meta`. Lit `/me` avec le jeton tout juste obtenu :
    la fiche dit QUEL compte est connecté."""
    me = _coeur().GraphClient(grant.access_token).get_me() or {}
    meta = {"email": me.get("mail") or me.get("userPrincipalName"),
            "name": me.get("displayName"),
            "scopes": grant.scope,
            "connected_at": _iso(datetime.now(timezone.utc))}
    entity_type, entity_id = _scope(org_id, sub)
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     grant.refresh_token, set_by=sub, meta=meta)
    _garder(entity_id, grant)
    logger.info("sharepoint : compte Microsoft connecté (org=%s)", org_id)
    return {"email": meta["email"], "name": meta["name"]}


def _row(org_id: int, sub: str) -> Optional[dict]:
    entity_type, entity_id = _scope(org_id, sub)
    return credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR)


def access_token_for(sub: str) -> str:
    """Un jeton d'accès valable pour la personne, renouvelé s'il expire dans moins
    d'une minute. Lève `RuntimeError` (aucun compte connecté, application non
    configurée) ou `MicrosoftReauthRequired` (autorisation morte : la ligne de
    coffre est marquée, la fiche dit « à reconnecter »)."""
    org_id = _ctx_org(sub)
    row = _row(org_id, sub)
    if not row or not row.get("secret"):
        from .. import config
        raise RuntimeError(
            "No Microsoft account connected. Sign in from your connectors page "
            f"({config.dashboard_url_for(sub)}/, connector « SharePoint & OneDrive »).")
    entity_type, entity_id = _scope(org_id, sub)
    refresh_token = row["secret"]
    with _VERROU:
        cached = _JETONS.get(_cle(entity_id, refresh_token))
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    coeur = _coeur()
    coordonnees = app()
    try:
        grant = coeur.auth.refresh(coordonnees["client_id"], coordonnees["client_secret"],
                                   refresh_token)
    except coeur.MicrosoftGrantExpired as e:
        message = ("Microsoft no longer accepts this sign-in (expired, revoked, or the "
                   "password changed). Reconnect from your connectors page, connector "
                   "« SharePoint & OneDrive ».")
        connector_health.mark_rejected(entity_type, entity_id, CONNECTOR, "", message)
        raise MicrosoftReauthRequired(message) from e
    # Le refresh token a tourné : le nouveau remplace l'ancien au coffre. La ligne
    # garde son identité ; un renouvellement réussi efface une marque de santé.
    meta = {k: v for k, v in (row.get("meta") or {}).items()
            if not k.startswith("health_")}
    if grant.refresh_token != refresh_token:
        credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                         grant.refresh_token, set_by=row.get("set_by"),
                                         meta=meta)
    _garder(entity_id, grant)
    return grant.access_token


# --- ce que la fiche affiche -------------------------------------------------

def _link_state(sub: str) -> connector_link.LinkState:
    from .. import access  # lazy

    org = access.current_org(sub)
    if org is None:
        return connector_link.LinkState(linked=False)
    row = _row(org, sub)
    if not row:
        return connector_link.LinkState(linked=False)
    meta = row.get("meta") or {}
    return connector_link.LinkState(
        linked=True, accounts=1, set_at=str(row.get("set_at") or "") or None,
        health_ko=True if meta.get("health_ko") else None,
        health_reason=meta.get("health_reason") or None)


connector_link.register(CONNECTOR, _link_state)


def _etape_manquante(sub: str, org, group, entry: dict) -> Optional[str]:
    """Ce qu'il reste à faire — et à QUI. Sans coordonnées d'application, ce n'est
    pas à la personne de cliquer « Se connecter » en boucle."""
    del org, group, entry
    if coordonnees_manquantes():
        return "Microsoft app to be configured by the operator"
    etat = _link_state(sub)
    if not etat.linked:
        return "Sign in with Microsoft"
    if etat.health_ko:
        return "Sign-in expired — reconnect"
    return None


status_hints.register(CONNECTOR, _etape_manquante)


def avertir_au_demarrage() -> None:
    """Dit AU BOOT ce qui empêchera le connecteur de servir. Ne lève jamais."""
    try:
        manquantes = coordonnees_manquantes()
    except Exception as e:  # noqa: SILENT — au boot la base peut n'être pas prête
        logger.info("sharepoint : configuration non vérifiable au démarrage (%s) — "
                    "le premier flux tranchera.", type(e).__name__)
        return
    if manquantes:
        logger.warning(
            "sharepoint : connecteur monté mais NON configuré — %s manquante(s). "
            "Le bouton « Se connecter » refusera en le disant. Poser : %s",
            ", ".join(manquantes), _COMMANDE)
