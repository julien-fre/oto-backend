"""Microsoft 365 — la connexion d'une PERSONNE (OAuth 2.0, permissions déléguées).

Flux hébergé par oto, sur le patron commun (`connectors/flow` + `auth/flow`), même
forme que `meta_ads` :

1. « Se connecter » sur la fiche → `_start_flow` rend l'URL du dialogue Microsoft,
   avec un `state` signé qui porte l'identité ;
2. Microsoft ramène le navigateur sur `/api/microsoft/oauth/callback`
   (`api/microsoft.py`) ; le code y devient un refresh token ;
3. le refresh token part au coffre, palier MEMBRE : la personne agit avec SES
   droits Microsoft 365, ni plus ni moins.

**Plusieurs comptes** (oto-backend#23). Une personne peut lier plusieurs comptes
Microsoft (son annuaire, celui d'un client…) : une ligne de coffre PAR COMPTE,
`account` = son adresse en minuscules, le compte Microsoft (`id` de `/me`) en meta.
Se connecter avec un autre compte en AJOUTE un ; avec le même, remplace le sien
(qu'il ait été renommé ou non). Le premier compte lié est le compte par défaut,
comme chez Google. Le reste est la mécanique GÉNÉRIQUE des connecteurs
multi-compte (`cardinality="multi"`) : choix par `_account=`, défaut par
`oto_identity(op='set')`, refus qui nomme les comptes sinon (`access.resolve`),
liste par `oto_identity(op='list')`, retrait d'un compte par
`DELETE /api/settings/api-keys/sharepoint?account=…`.

**Renouvellement.** Un jeton d'accès vit une heure ; `access_token_for` le
redemande avec le refresh token, et ⚠️ Entra fait TOURNER ce dernier : le nouveau
remplace l'ancien au coffre à chaque renouvellement, sur la ligne de CE compte. Le
jeton d'accès lui-même ne va jamais en base : il vit dans un cache du process, keyé
par le hash de la ligne et de son refresh token (une reconnexion l'invalide
d'elle-même). Une autorisation morte marque la ligne de CE compte, pas les autres.

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


def _cle(ligne: tuple, refresh_token: str) -> str:
    """Clé du cache : la ligne (entité ET compte) et son refresh token, hachés
    (jamais un secret en clair comme clé). Une reconnexion change le refresh token,
    donc la clé."""
    return hashlib.sha256("|".join((*ligne, refresh_token)).encode()).hexdigest()


def _garder(ligne: tuple, grant) -> None:
    with _VERROU:
        _JETONS[_cle(ligne, grant.refresh_token)] = (
            grant.access_token, time.time() + int(grant.expires_in))


def _comptes(org_id: int, sub: str) -> list[dict]:
    """Les comptes Microsoft liés par la personne dans cette org (sans secret)."""
    entity_type, entity_id = _scope(org_id, sub)
    return credentials_store.list_accounts(entity_type, entity_id, CONNECTOR)


def persist_grant(sub: str, org_id: int, grant) -> dict:
    """Range le compte qui vient de se connecter, À CÔTÉ des autres : le refresh
    token est le secret (`secret_kind="oauth"`), l'identité lue sur `/me` va dans
    `meta`.

    Le compte se reconnaît à son `id` Microsoft, pas à son nom de ligne : une
    reconnexion du même compte remplace SA ligne, même renommée
    (`oto_identity(op='rename')`) ; un autre compte en crée une, nommée par son
    adresse en minuscules. Le premier compte lié est le défaut (règle Google) ; une
    reconnexion garde le statut de défaut de la ligne et efface sa marque de santé."""
    me = _coeur().GraphClient(grant.access_token).get_me() or {}
    microsoft_id = me.get("id")
    adresse = (me.get("mail") or me.get("userPrincipalName") or "").strip()
    if not microsoft_id or not adresse:
        raise RuntimeError(
            "Microsoft n'a pas dit quel compte vient de se connecter (`/me` sans `id` "
            "ou sans adresse) : rien n'a été enregistré.")
    entity_type, entity_id = _scope(org_id, sub)
    comptes = _comptes(org_id, sub)
    deja = next((c for c in comptes
                 if (c.get("meta") or {}).get("microsoft_id") == microsoft_id), None)
    if deja is not None:
        account = deja["account"]
    else:
        account = adresse.lower()
        if any(c["account"] == account for c in comptes):
            # Un autre compte Microsoft a été renommé de cette adresse : l'écraser
            # perdrait sa connexion.
            raise RuntimeError(
                f"Un autre compte Microsoft lié porte déjà le nom `{account}` : "
                "renomme-le (oto_identity op='rename') puis reconnecte celui-ci. "
                "Rien n'a été enregistré.")
    ancien = (deja or {}).get("meta") or {}
    meta = {**{k: v for k, v in ancien.items() if not k.startswith("health_")},
            "email": adresse,
            "name": me.get("displayName"),
            "microsoft_id": microsoft_id,
            "scopes": grant.scope,
            "connected_at": _iso(datetime.now(timezone.utc)),
            "is_default": bool(ancien.get("is_default")) if deja else not comptes}
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     grant.refresh_token, set_by=sub, meta=meta,
                                     account=account)
    _garder((entity_type, entity_id, account), grant)
    logger.info("sharepoint : compte Microsoft %s (org=%s)",
                "reconnecté" if deja else "ajouté", org_id)
    return {"account": account, "email": adresse, "name": meta["name"]}


def _ranger_rotation(ligne: tuple, lu: str, nouveau: str) -> None:
    """Le refresh token a tourné : le nouveau remplace l'ancien sur la ligne de CE
    compte, son `meta` repassé tel quel (l'upsert l'écraserait sinon). Écriture
    conditionnelle : un appel concurrent qui a déjà tourné a la valeur la plus
    récente, on ne la remplace pas par la nôtre."""
    entity_type, entity_id, account = ligne
    row = credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR,
                                                     account=account)
    if not row or row.get("secret") != lu:
        return
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR, nouveau,
                                     set_by=row.get("set_by"), meta=row.get("meta") or {},
                                     account=account)


def access_token_for(sub: str) -> str:
    """Un jeton d'accès valable pour la personne, sur le compte que l'appel désigne,
    renouvelé s'il expire dans moins d'une minute.

    Le compte est choisi par la résolution COMMUNE des connecteurs multi-compte
    (`access.resolve_credential`) : `_account=` de l'appel, sinon le compte épinglé
    par le projet, sinon le seul compte lié, sinon le compte par défaut, sinon un
    refus qui nomme les comptes. Lève une `McpError` (aucun compte, compte inconnu,
    ambiguïté), `RuntimeError` (application non configurée) ou
    `MicrosoftReauthRequired` (autorisation morte : la ligne de CE compte est
    marquée, la fiche dit « à reconnecter »)."""
    from .. import access  # lazy : évite tout cycle d'import au boot

    rc = access.resolve_credential(CONNECTOR, want="byo", sub=sub)
    ligne = (rc.entity_type, rc.entity_id, rc.account)
    refresh_token = rc.key
    with _VERROU:
        cached = _JETONS.get(_cle(ligne, refresh_token))
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    coeur = _coeur()
    coordonnees = app()
    try:
        grant = coeur.auth.refresh(coordonnees["client_id"], coordonnees["client_secret"],
                                   refresh_token)
    except coeur.MicrosoftGrantExpired as e:
        message = (f"Microsoft no longer accepts the sign-in of `{rc.account}` (expired, "
                   "revoked, or the password changed). Reconnect this account from your "
                   "connectors page, connector « SharePoint & OneDrive ».")
        connector_health.mark_rejected(rc.entity_type, rc.entity_id, CONNECTOR,
                                       rc.account, message)
        raise MicrosoftReauthRequired(message) from e
    if grant.refresh_token != refresh_token:
        _ranger_rotation(ligne, refresh_token, grant.refresh_token)
    # Un renouvellement réussi efface la marque « à reconnecter » de CE compte.
    connector_health.record_health(CONNECTOR, ligne, True, None)
    _garder(ligne, grant)
    return grant.access_token


# --- ce que la fiche affiche -------------------------------------------------

def _comptes_du_contexte(sub: str) -> tuple[list[dict], list[dict]]:
    """`(comptes, morts)` de la personne dans son org de contexte — `morts` = ceux
    dont l'autorisation est tombée (`health_ko`), à reconnecter un par un."""
    from .. import access  # lazy

    org = access.current_org(sub)
    comptes = _comptes(org, sub) if org is not None else []
    return comptes, [c for c in comptes if (c.get("meta") or {}).get("health_ko")]


def _link_state(sub: str) -> connector_link.LinkState:
    """Lié dès un compte ; « à reconnecter » si l'un d'eux l'est, en le nommant."""
    comptes, morts = _comptes_du_contexte(sub)
    if not comptes:
        return connector_link.LinkState(linked=False)
    return connector_link.LinkState(
        linked=True, accounts=len(comptes),
        set_at=max((str(c.get("set_at") or "") for c in comptes), default="") or None,
        health_ko=True if morts else None,
        health_reason="; ".join(
            f"{c['account']}: {(c.get('meta') or {}).get('health_reason') or 'rejected'}"
            for c in morts) or None)


connector_link.register(CONNECTOR, _link_state)


def _etape_manquante(sub: str, org, group, entry: dict) -> Optional[str]:
    """Ce qu'il reste à faire — et à QUI. Sans coordonnées d'application, ce n'est
    pas à la personne de cliquer « Se connecter » en boucle. Un compte mort parmi
    plusieurs se nomme : les autres continuent de servir."""
    del org, group, entry
    if coordonnees_manquantes():
        return "Microsoft app to be configured by the operator"
    comptes, morts = _comptes_du_contexte(sub)
    if not comptes:
        return "Sign in with Microsoft"
    if morts:
        return f"Sign-in expired for {', '.join(c['account'] for c in morts)} — reconnect"
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
