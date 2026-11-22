"""Les VERBES du consentement OAuth per-user de **google** : démarrer, lire l'état,
révoquer, élire le compte par défaut.

- `GET    /api/google/oauth/start`   → `{auth_url}` à ouvrir
- `GET    /api/google/oauth/status`  → l'état du consentement (multi-compte)
- `DELETE /api/google/oauth`         → révoque
- `POST   /api/google/oauth/default` → élit le compte Google par défaut
- `POST   /api/google/oauth/complete` → termine un consentement, AVEC le jeton de qui l'a
  démarré (Google CASA, ADA 3.2.2 : le callback ne range plus rien, cf. `_google_complete`)

⚠️ **Ce module a porté `atlassian` et `folkmcp` jusqu'au 2026-09-09** — d'où son nom
et les clés de capacité `me.federation.google.*`, gardés tels quels : ce sont des
identifiants servis, et les renommer serait une rupture de contrat sans contrepartie.
La **fédération MCP est retirée de la plateforme** (ADR 0069, `kind="mount"`) ; il ne
reste ici que google, qui n'a JAMAIS été fédéré — c'est un connecteur natif dont le
credential s'acquiert par OAuth, exactement comme salesforce ou zoho.

⚠️ **Les CALLBACKS ne vivent pas ici** (`…/oauth/callback`) : le fournisseur y redirige
le NAVIGATEUR, sans en-tête d'auth, et la réponse est une **302**, pas du JSON.
L'adaptateur REST authentifie toujours et répond toujours en JSON : ils sont hors du
moule par construction, et restent classés par NATURE dans leurs modules.

**Pas de face MCP** (`mcp=None`) : ouvrir une page de consentement demande un
navigateur, et le pendant agent générique existe déjà (`me.connector_connect`). Un
second chemin par fournisseur recréerait le doublon que ce chantier supprime.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from pydantic import BaseModel

from .. import access, db
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)


# --- Entrées ----------------------------------------------------------------

class OAuthStartInput(BaseModel):
    """Aucun paramètre : le consentement est demandé pour le porteur du jeton."""


class OAuthStatusInput(BaseModel):
    """Aucun paramètre."""


class GoogleRevokeInput(BaseModel):
    # Compte précis à révoquer ; ABSENT = TOUS les comptes Google du sub.
    account: Optional[str] = None
    # `org` / `group` : les comptes PARTAGÉS de ce scope (admin d'org / chef d'équipe).
    scope: Optional[str] = None


class GoogleDefaultInput(BaseModel):
    account: str = ""
    scope: Optional[str] = None             # `org` / `group` : le défaut PARTAGÉ


class GoogleCompleteInput(BaseModel):
    """Ce que le callback a remis au front dans le FRAGMENT de l'URL de retour
    (`#code=…&state=…`), tel quel."""
    code: str
    state: str


# --- Sorties ----------------------------------------------------------------

class OAuthStart(BaseModel):
    """L'URL de consentement à OUVRIR dans un navigateur. Rien n'est connecté tant que
    l'utilisateur n'y est pas passé — c'est le callback du fournisseur qui persiste."""
    auth_url: str


class FederationDisconnected(BaseModel):
    """`disconnected: false` = il n'y avait rien à déconnecter (idempotent), pas un échec.

    ⚠️ **Aucune capacité de CE fichier ne la produit** — elle est importée telle quelle
    par `capabilities/connectors/oauth_status.py::me.connector_disconnect`, qui la
    réutilise comme contrat de sortie. Ne pas supprimer en la croyant morte : le retrait
    de la fédération (2026-09-09) n'y a rien changé, `me.connector_disconnect` sert
    toujours google."""
    ok: bool
    disconnected: bool


class GoogleAccount(BaseModel):
    email: Optional[str] = None
    is_default: bool = False
    scopes: list[str] = []
    granted_at: Optional[str] = None
    # Les services que CE compte a autorisés (split du 2026-09-26) — dérivés des
    # scopes, pour qu'un front n'ait pas à connaître les URLs de scope de Google.
    services: list[str] = []
    # À qui le compte est confié : `member` (le sien), `group` ou `org` (partagé).
    scope: str = "member"
    # Compte partagé : le `sub` de l'admin qui l'a connecté (vide pour le sien).
    connected_by: Optional[str] = None


class GoogleStatus(BaseModel):
    """⚠️ **Les champs racine décrivent le compte PAR DÉFAUT, pas l'union des comptes.**
    C'est un héritage du temps où Google était mono-compte, conservé pour compat :
    `granted_at`/`scopes` valent pour le défaut seul. La vérité multi-compte est
    `accounts`, et un intégrateur doit lire celle-là."""
    connected: bool
    granted_at: Optional[str] = None
    scopes: list[str] = []
    accounts: list[GoogleAccount]
    # Les comptes PARTAGÉS joignables par l'appelant (son équipe active, son org) —
    # ses outils les résolvent après les siens (2026-09-27).
    shared: list[GoogleAccount] = []


class GoogleRevoked(BaseModel):
    """`account: null` veut dire que TOUS les comptes ont été révoqués, pas « aucun »."""
    ok: bool
    account: Optional[str] = None


class GoogleDefaultSet(BaseModel):
    ok: bool
    default: str


class GoogleConsentCompleted(BaseModel):
    """Le compte Google est rangé : `account` = l'adresse qui a consenti, `connector` =
    la carte qui l'a demandé (le compte, ou l'un de ses services), `scope` = à qui il
    est confié (`member`, ou `org`/`group` pour un compte partagé)."""
    ok: bool
    connector: str
    account: str
    scope: str


# --- Google (multi-compte) --------------------------------------------------

def _google_start(ctx: ResolvedCtx, inp: OAuthStartInput) -> dict:
    from ..auth import google as google_oauth
    try:
        url = google_oauth.build_auth_url(ctx.sub)
    except RuntimeError as e:
        # Forme historique EXACTE, espace compris : le code machine porte la cause.
        raise AuthzDenied(500, f"oauth_misconfigured: {e}")
    return {"auth_url": url}


def _google_complete(ctx: ResolvedCtx, inp: GoogleCompleteInput) -> dict:
    """Termine un consentement Google — le geste que le callback faisait seul.

    L'ORDRE est le contrat :
    1. le state se lit (signature en temps constant, audience `google`, 10 min, `jti`) ;
    2. son `sub` doit être CELUI DU PORTEUR : le state lie le consentement à la personne
       qui l'a démarré, le jeton lie la requête à la personne qui le termine — un state
       de A présenté par B est refusé, et rien n'est consommé ni échangé ;
    3. il se consomme (usage unique) AVANT l'échange : deux complétions concurrentes du
       même retour n'échangent pas deux fois, un rejeu est refusé ;
    4. seulement alors le code s'échange (avec l'app et le rappel exact qui ont demandé
       le consentement, `app_for(sub)` — tenant compris) et le jeton se range, pour
       l'org et la carte que le state porte.

    Le code et le state ne sont jamais journalisés."""
    from ..auth import google as google_oauth
    data = google_oauth.read_state(inp.state)
    if data is None:
        raise AuthzDenied(400, "invalid_state",
                          "This Google connection link is invalid or has expired. "
                          "Start the connection again.")
    if not hmac.compare_digest(data["sub"].encode(), (ctx.sub or "").encode()):
        logger.warning("google oauth complete: state d'une autre personne refusé "
                       "(porteur=%s)", ctx.sub)
        raise AuthzDenied(403, "state_not_yours",
                          "This Google connection was started by another account. "
                          "Start it again from your own session.")
    if not google_oauth.consume_state(data):
        raise AuthzDenied(409, "state_used",
                          "This Google connection link has already been used. "
                          "Start the connection again.")
    sub, org_id, scope, connector = data["sub"], data["org"], data["scope"], data["connector"]
    try:
        tokens = google_oauth.exchange_code(inp.code, sub)
        if scope == "member":
            email = google_oauth.persist_token(sub, org_id, tokens)
        else:
            email = google_oauth.persist_token(sub, org_id, tokens, scope=scope,
                                               group_id=data["group"], connector=connector)
    except Exception:
        # Le diagnostic va au journal (jamais le code) ; l'appelant reçoit un refus
        # nommé, et sait que recommencer la connexion est la seule suite.
        logger.exception("google oauth complete en échec (sub=%s org=%s)", sub, org_id)
        raise AuthzDenied(502, "exchange_failed",
                          "Google did not complete the connection. Start it again.")
    return {"ok": True, "connector": connector, "account": email, "scope": scope}


def _google_status(ctx: ResolvedCtx, inp: OAuthStatusInput) -> dict:
    from ..auth import google as google_oauth
    accounts = google_oauth.list_accounts(ctx.sub)
    default = next((a for a in accounts if a.get("is_default")), None)
    return {
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
                "services": google_oauth.services_granted(a.get("scopes")),
            }
            for a in accounts
        ],
        "shared": [
            {
                "email": a.get("google_email"),
                "is_default": a.get("is_default", False),
                "scopes": a["scopes"].split() if a.get("scopes") else [],
                "granted_at": a.get("granted_at"),
                "services": google_oauth.services_granted(a.get("scopes")),
                "scope": a.get("scope") or "org",
                "connected_by": a.get("connected_by"),
            }
            for a in google_oauth.list_shared_accounts(ctx.sub)
        ],
    }


def _google_revoke(ctx: ResolvedCtx, inp: GoogleRevokeInput) -> dict:
    from ..auth import google as google_oauth
    # ?account=<email> révoque un compte précis ; absent = tous.
    account = inp.account or None
    scope = inp.scope or "member"
    try:
        if scope == "member":
            google_oauth.revoke(ctx.sub, account=account)
        else:
            google_oauth.revoke(ctx.sub, account=account, scope=scope)
    except PermissionError as e:
        raise AuthzDenied(403, "scope_forbidden", str(e))
    except ValueError as e:
        raise AuthzDenied(400, "invalid_scope", str(e))
    return {"ok": True, "account": account}


def _google_set_default(ctx: ResolvedCtx, inp: GoogleDefaultInput) -> dict:
    account = (inp.account or "").strip()
    if not account:
        raise AuthzDenied(400, "missing_account")
    if inp.scope and inp.scope != "member":
        from ..auth import google as google_oauth
        try:
            if not google_oauth.set_default_shared(ctx.sub, account, inp.scope):
                raise AuthzDenied(404, "unknown_account")
        except PermissionError as e:
            raise AuthzDenied(403, "scope_forbidden", str(e))
        except ValueError as e:
            raise AuthzDenied(400, "invalid_scope", str(e))
        return {"ok": True, "default": account}
    org_id = access.current_org(ctx.sub)
    if org_id is None or not db.set_default_google_account(ctx.sub, org_id, account):
        raise AuthzDenied(404, "unknown_account")
    return {"ok": True, "default": account}


_DOC_G_START = (
    "Rend l'URL de consentement Google à ouvrir. ⚠️ Un `500 oauth_misconfigured:` "
    "signale une app OAuth mal configurée CÔTÉ PLATEFORME, pas une erreur de l'appelant."
)
_DOC_G_STATUS = (
    "Mes comptes Google connectés. ⚠️ Les champs racine (`granted_at`, `scopes`) "
    "décrivent le compte PAR DÉFAUT seul — héritage du temps où Google était "
    "mono-compte. La vérité multi-compte est `accounts`."
)
_DOC_G_REVOKE = (
    "Révoque un compte Google précis (`?account=<email>`) ou, SANS paramètre, TOUS mes "
    "comptes Google. `account: null` dans la réponse veut donc dire « tous », pas « aucun »."
)
_DOC_G_DEFAULT = (
    "Élit le compte Google par défaut de mon org de contexte — celui qu'utilisent les "
    "outils quand aucun compte n'est nommé à l'appel."
)

_DOC_G_COMPLETE = (
    "Termine un consentement Google : le front poste le `code` et le `state` que le "
    "callback lui a remis dans le fragment (`connect=confirm`). Le state doit avoir été "
    "émis pour le PORTEUR du jeton (`state_not_yours` sinon), il ne sert qu'une fois "
    "(`state_used`), puis le code est échangé et le compte rangé."
)
_ERREURS_G_COMPLETE = (
    DeclaredError(400, "invalid_state", "state illisible, altéré, expiré ou d'un autre flux"),
    DeclaredError(403, "state_not_yours", "state émis pour une autre personne que le porteur"),
    DeclaredError(409, "state_used", "state déjà consommé (rejeu)"),
    DeclaredError(502, "exchange_failed", "Google a refusé l'échange du code, ou le rangement a échoué"),
)

CAPABILITIES += [
        Capability(
            key="me.federation.google.start", handler=_google_start,
            Input=OAuthStartInput, authz=SUB_ONLY, Output=OAuthStart, mcp=None,
            description=_DOC_G_START,
            rest=RestBinding("GET", "/api/google/oauth/start"),
        ),
        Capability(
            key="me.federation.google.status", handler=_google_status,
            Input=OAuthStatusInput, authz=SUB_ONLY, Output=GoogleStatus, mcp=None,
            description=_DOC_G_STATUS,
            rest=RestBinding("GET", "/api/google/oauth/status"),
        ),
        Capability(
            key="me.federation.google.revoke", handler=_google_revoke,
            Input=GoogleRevokeInput, authz=SUB_ONLY, Output=GoogleRevoked, mcp=None,
            description=_DOC_G_REVOKE,
            rest=RestBinding("DELETE", "/api/google/oauth"),
        ),
        Capability(
            key="me.federation.google.set_default", handler=_google_set_default,
            Input=GoogleDefaultInput, authz=SUB_ONLY, Output=GoogleDefaultSet, mcp=None,
            description=_DOC_G_DEFAULT,
            rest=RestBinding("POST", "/api/google/oauth/default"),
        ),
        Capability(
            key="me.federation.google.complete", handler=_google_complete,
            Input=GoogleCompleteInput, authz=SUB_ONLY, Output=GoogleConsentCompleted,
            errors=_ERREURS_G_COMPLETE, mcp=None,
            description=_DOC_G_COMPLETE,
            rest=RestBinding("POST", "/api/google/oauth/complete"),
        ),
]
