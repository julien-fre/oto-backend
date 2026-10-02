"""Notion — connexion par consentement (« Connecter avec Notion »), à côté du jeton collé.

Deux modes coexistent, et c'est ce qui rend l'ajout peu invasif (patron Zoho) : ils
produisent **le même credential** — un jeton Bearer, rangé comme la clé d'une
intégration interne. Les outils (`tools/notion.py`) et la cascade ne voient aucune
différence. Seule la façon de l'OBTENIR change :

- **jeton collé** (existant, inchangé) : l'utilisateur crée une intégration interne,
  colle son token, puis partage chaque page avec elle À LA MAIN — étape oubliée
  presque à chaque fois (d'où l'avertissement du zéro de `notion_search`) ;
- **consentement** (ce module) : un clic, l'écran Notion où l'utilisateur CHOISIT les
  pages à partager, un clic. Le partage est fait au consentement.

**D'où vient l'app ?** Une intégration PUBLIQUE Notion, posée comme **app d'éditeur**
(`credentials_store` §app d'éditeur) — jamais dans le code ni l'environnement, ce
dépôt est public. Deux crans, dans cet ordre :

1. l'app du TENANT de la personne (`editor:tenant:<slug>`, posée depuis sa surface
   admin, `capabilities/tenant_apps.py`) — le consentement s'affiche sous SA marque,
   et le rappel part sur SON host (`tenancy.callback_host`) ;
2. l'app de l'instance (`editor:global`, `platform.editor_app.set`).

Aucune des deux ⟹ pas de bouton prêt (`app_disponible`), et le démarrage refuse en
le disant : le jeton collé reste alors la voie.

**Transport** : délégué à `auth/flow` (state signé lié à son audience, échange
rédigé). Notion exige l'app en **HTTP Basic** et un corps **JSON**
(`client_auth="basic_json"`), et `owner=user` sur l'URL d'autorisation.

**Durée de vie** : la doc de l'API REST Notion ne publie aucune expiration, mais
documente un refresh qui ROTE le refresh token. On ne parie donc pas : le refresh
token est rangé CHIFFRÉ (`meta.refresh_token_enc`), l'échéance seulement si Notion
en donne une (`expires_in`), et `fresh_key` renouvelle à l'usage quand elle approche.
Sans échéance, rien ne se renouvelle — le jeton vit tant que Notion le garde.
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import credentials_store, crypto
from ..connectors import flow as connector_flow
from . import flow as oauth_flow

logger = logging.getLogger(__name__)

CONNECTOR = "notion"

_AUD = "notion"
_CALLBACK_PATH = "/api/notion/oauth/callback"
_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
_CLIENT_AUTH = "basic_json"

#: Clé de l'app d'éditeur de l'INSTANCE (Notion n'a pas de régions).
PLATFORM_APP_KEY = "global"

#: Marge de renouvellement : un jeton qui expire dans moins que ça est renouvelé
#: avant l'appel, pas après son échec.
_RENEW_MARGIN = timedelta(minutes=5)

_SCOPES = ("member", "org", "group")


class NotionOAuthError(RuntimeError):
    """Échec d'acquisition ou de renouvellement — message SÛR à afficher."""


# --- l'app (client_id / client_secret) ------------------------------------------

def _tenant_slug(sub: str) -> Optional[str]:
    """Le tenant de la personne, s'il n'est pas le primaire (le primaire n'a pas
    d'app propre : la sienne EST celle de l'instance, même règle que google)."""
    from .. import tenancy  # lazy : évite tout cycle d'import au boot
    slug = tenancy.current().tenant_of(sub)
    return slug if slug and slug != tenancy.primary_slug() else None


def _app_key_for(sub: str) -> Optional[str]:
    """La clé de l'app à employer pour ce sub, ou None si aucune n'est posée.

    ⚠️ Une erreur de coffre REMONTE : seule l'ABSENCE fait descendre d'un cran
    (même leçon que `google.app_for`) — sinon l'utilisateur d'un tenant consentirait
    sous notre marque sans que rien ne le dise."""
    slug = _tenant_slug(sub)
    if slug:
        key = credentials_store.tenant_app_key(slug)
        if credentials_store.get_editor_app(CONNECTOR, key):
            return key
    if credentials_store.get_editor_app(CONNECTOR, PLATFORM_APP_KEY):
        return PLATFORM_APP_KEY
    return None


def _app(key: str) -> dict:
    app = credentials_store.get_editor_app(CONNECTOR, key)
    if not app:
        raise NotionOAuthError(
            "L'application Notion qui a émis cette connexion n'est plus posée sur "
            "l'instance — reconnecte Notion.")
    return app


def _redirect_for(key: str) -> str:
    """Le rappel enregistré chez Notion pour CETTE app : le host du tenant pour une
    app de tenant, celui de l'instance sinon. L'aller et l'échange doivent envoyer
    le même, au byte près."""
    from .. import tenancy
    slug = credentials_store.tenant_of_app_key(key)
    host = tenancy.current().callback_host(slug) if slug else None
    return oauth_flow.redirect_uri(_CALLBACK_PATH, host=host)


def app_disponible(sub: str) -> bool:
    """`app_ready` du descripteur de flux : le bouton peut-il aboutir pour ce sub ?"""
    return _app_key_for(sub) is not None


# --- le state signé ---------------------------------------------------------------

def make_state(sub: str, org_id: int, scope: str, group_id: Optional[int],
               app_key: str, return_app: str = "") -> str:
    """Le callback arrive SANS en-tête d'auth : qui consent, pour quel palier, avec
    quelle app, et vers quel front revenir doivent voyager dans le state signé.
    `return_app` est déjà réduit à la liste fermée par l'appelant."""
    return oauth_flow.sign_state(_AUD, {"sub": sub, "org": org_id, "s": scope,
                                        "g": group_id, "k": app_key, "a": return_app})


def verify_state(state: Optional[str]) -> Optional[dict]:
    """Le payload si le state est valide, de CE flux, et non expiré ; None sinon."""
    d = oauth_flow.read_state(_AUD, state)
    if not d:
        return None
    if not isinstance(d.get("sub"), str) or not isinstance(d.get("org"), int):
        return None
    if d.get("s") not in _SCOPES or not isinstance(d.get("k"), str) or not d["k"]:
        return None
    if d["s"] == "group" and not isinstance(d.get("g"), int):
        return None
    return {"sub": d["sub"], "org": d["org"], "scope": d["s"], "group": d.get("g"),
            "app_key": d["k"],
            "return_app": d.get("a") if isinstance(d.get("a"), str) else ""}


# --- démarrage ---------------------------------------------------------------------

def _check_scope(sub: str, org_id: int, scope: str) -> Optional[int]:
    """Le droit de ranger la connexion à ce palier ; rend l'équipe pour `group`.
    Vérifié au démarrage ET au retour (le state vit 10 min)."""
    from .. import access, detenteurs, roles  # lazy
    if scope == "org":
        if not roles.is_org_admin(sub, org_id):
            raise PermissionError(
                "Seul un admin de l'org peut connecter Notion pour toute l'org."
                + detenteurs.phrase("Ses administrateurs, à qui le demander",
                                    detenteurs.admins_de_l_org(sub, org_id)))
        return None
    if scope == "group":
        group_id = access.current_group(sub)
        if group_id is None:
            raise PermissionError(
                "Aucune équipe active — sélectionne une équipe pour la connecter.")
        if not roles.can_admin_group(sub, group_id):
            raise PermissionError(
                "Seul un chef d'équipe peut connecter Notion pour toute l'équipe."
                + detenteurs.phrase("Chefs de cette équipe",
                                    detenteurs.chefs_de_l_equipe(sub, group_id, org_id)))
        return group_id
    return None


def build_auth_url(sub: str, org_id: int, scope: str = "member",
                   return_app: str = "") -> str:
    """L'URL de l'écran de consentement Notion (avec son sélecteur de pages)."""
    if scope not in _SCOPES:
        raise ValueError(f"scope invalide : {scope!r} (member, org ou group)")
    group_id = _check_scope(sub, org_id, scope)
    key = _app_key_for(sub)
    if key is None:
        raise LookupError(
            "La connexion en un clic n'est pas configurée sur cette instance : colle "
            "plutôt le jeton d'une intégration interne Notion.")
    q = urllib.parse.urlencode({
        "client_id": _app(key)["client_id"],
        "redirect_uri": _redirect_for(key),
        "response_type": "code",
        "owner": "user",
        "state": make_state(sub, org_id, scope, group_id, key,
                            oauth_flow.resolve_return_app(return_app)),
    })
    return f"{_AUTHORIZE_URL}?{q}"


def _start_flow(ctx, values: dict) -> connector_flow.FlowStart:
    """Le geste « connecter », déclaré comme celui de tout autre connecteur. `scope`
    et `app` sont des clés CACHÉES : la surface (fiche perso, org, équipe) et le
    front les passent, l'utilisateur ne les choisit pas."""
    from ..capabilities._types import AuthzDenied
    values = values or {}
    scope = values.get("scope") or "member"
    if not ctx.org_id:
        raise AuthzDenied(400, "no_org_context",
                          "Aucune org de contexte — impossible de scoper la connexion "
                          "Notion. Reconnecte-toi et réessaie.")
    try:
        url = build_auth_url(ctx.sub, ctx.org_id, scope, values.get("app") or "")
    except ValueError as e:
        raise AuthzDenied(400, "invalid_scope_param", str(e))
    except PermissionError as e:
        raise AuthzDenied(403, "org_admin_required", str(e))
    except LookupError as e:
        raise AuthzDenied(503, "oauth_misconfigured", str(e))
    return connector_flow.FlowStart(auth_url=url, details={"scope": scope})


connector_flow.declare(
    CONNECTOR,
    start=_start_flow,
    label="Connecter avec Notion",
    callback_path=_CALLBACK_PATH,
    app_ready=app_disponible,
    # Le consentement donne à lui seul un credential complet : le formulaire du
    # jeton est une AUTRE voie, pas une première étape (≠ zoho/salesforce).
    standalone=True,
)


# --- échange et rangement --------------------------------------------------------

def exchange_code(code: str, app_key: str) -> dict:
    app = _app(app_key)
    try:
        tokens = oauth_flow.exchange_code(
            _TOKEN_URL, code=code, client_id=app["client_id"],
            client_secret=app["client_secret"], redirect=_redirect_for(app_key),
            client_auth=_CLIENT_AUTH)
    except oauth_flow.OAuthFlowError as e:
        raise NotionOAuthError(str(e)) from e
    if not tokens.get("access_token"):
        raise NotionOAuthError("Notion n'a pas renvoyé de jeton d'accès.")
    return tokens


def _entity(sub: str, org_id: int, scope: str,
            group_id: Optional[int]) -> tuple[str, str]:
    if scope == "org":
        return credentials_store.ORG, str(org_id)
    if scope == "group":
        return "group", str(group_id)
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


def _refresh_aad(entity_type: str, entity_id: str) -> str:
    """AAD du refresh token chiffré. Lié à l'entité, PAS au compte : un compte se
    renomme (`ensure_named_coexistence` migre '' → « principal »), et un AAD qui
    portait le nom rendrait le refresh token indéchiffrable au premier renommage."""
    return f"notion-refresh:{entity_type}:{entity_id}"


def _token_meta(entity_type: str, entity_id: str, tokens: dict,
                now: datetime) -> dict:
    """Satellites rangés avec le jeton : ce qui dit QUEL espace est connecté, et de
    quoi le renouveler si Notion le fait expirer."""
    meta: dict = {}
    if tokens.get("refresh_token"):
        meta["refresh_token_enc"] = crypto.encrypt(
            tokens["refresh_token"], _refresh_aad(entity_type, entity_id))
    if isinstance(tokens.get("expires_in"), (int, float)):
        meta["expires_at"] = (now + timedelta(seconds=tokens["expires_in"])).isoformat()
    return meta


def _account_for(entity_type: str, entity_id: str, workspace_id: str,
                 workspace_name: str) -> str:
    """Le compte où ranger cette connexion. Reconnecter le MÊME espace remplace sa
    ligne — repéré par `workspace_id`, jamais par le nom, qui se renomme côté
    Notion. Un espace neuf prend son nom (suffixé s'il est déjà pris)."""
    rows = credentials_store.list_accounts(entity_type, entity_id, CONNECTOR)
    for r in rows:
        if workspace_id and (r.get("meta") or {}).get("workspace_id") == workspace_id:
            return r["account"]
    base = (workspace_name or "").strip() or "notion"
    taken = {r["account"] for r in rows}
    name, i = base, 2
    while name in taken:
        name, i = f"{base} ({i})", i + 1
    return name


def persist(sub: str, org_id: int, scope: str, group_id: Optional[int],
            app_key: str, tokens: dict) -> str:
    """Range le jeton au palier demandé et rend le compte écrit. Synchrone (DB) :
    l'appelant l'exécute hors de la boucle.

    Le compte écrit devient le DÉFAUT du palier : une personne qui avait collé un
    jeton puis se connecte en un clic garde l'ancien (rien n'est détruit), mais ses
    appels passent par le nouveau — sinon deux comptes sans défaut rendraient chaque
    appel ambigu (`cascade._shared_auto_account`)."""
    _check_scope(sub, org_id, scope)  # re-vérifié : le droit a pu tomber en 10 min
    entity_type, entity_id = _entity(sub, org_id, scope, group_id)
    workspace_id = str(tokens.get("workspace_id") or "")
    account = _account_for(entity_type, entity_id, workspace_id,
                           str(tokens.get("workspace_name") or ""))
    credentials_store.guard_account_write(entity_type, entity_id, CONNECTOR, account,
                                          org=org_id)
    now = datetime.now(timezone.utc)
    meta = {
        "acquired_via": "oauth", "app": app_key, "connected_at": now.isoformat(),
        "workspace_id": workspace_id or None,
        "workspace_name": tokens.get("workspace_name") or None,
        "workspace_icon": tokens.get("workspace_icon") or None,
        "bot_id": tokens.get("bot_id") or None,
        "is_default": True,
        **_token_meta(entity_type, entity_id, tokens, now),
    }
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     tokens["access_token"], set_by=sub, meta=meta,
                                     account=account)
    for r in credentials_store.list_accounts(entity_type, entity_id, CONNECTOR):
        if r["account"] != account and (r.get("meta") or {}).get("is_default"):
            credentials_store.update_meta(entity_type, entity_id, CONNECTOR,
                                          r["account"], {"is_default": False})
    return account


# --- renouvellement à l'usage -----------------------------------------------------

def _due(meta: dict, now: datetime) -> bool:
    raw = meta.get("expires_at")
    if not raw:
        return False
    try:
        expires_at = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    return expires_at - now <= _RENEW_MARGIN


def fresh_key(rc) -> str:
    """Le jeton à employer pour ce credential résolu — renouvelé d'abord s'il vient
    d'un consentement et que son échéance approche. Tout le reste (jeton collé,
    palier plateforme, aucune échéance connue) : la clé telle quelle, sans lecture
    de plus que celle de sa ligne."""
    if rc.is_platform or not rc.entity_type:
        return rc.key
    row = credentials_store.get_credential_with_meta(
        rc.entity_type, rc.entity_id, CONNECTOR, rc.account)
    meta = (row or {}).get("meta") or {}
    now = datetime.now(timezone.utc)
    if meta.get("acquired_via") != "oauth" or not _due(meta, now):
        return rc.key
    return _renew(rc.entity_type, rc.entity_id, rc.account, meta, now)


def _renew(entity_type: str, entity_id: str, account: str, meta: dict,
           now: datetime) -> str:
    """Échange le refresh token et range la paire NEUVE — Notion rote le refresh
    token, celui qu'on vient d'envoyer ne vaut plus rien. `meta` re-passé complet :
    `set_credential` sans `meta` l'écrase par `{}`."""
    if not meta.get("refresh_token_enc"):
        raise NotionOAuthError(
            "La connexion Notion a expiré et ne peut pas se renouveler — reconnecte "
            "Notion.")
    refresh = crypto.decrypt(meta["refresh_token_enc"],
                             _refresh_aad(entity_type, entity_id))
    app = _app(meta.get("app") or PLATFORM_APP_KEY)
    try:
        tokens = oauth_flow.refresh_grant(
            _TOKEN_URL, refresh_token=refresh, client_id=app["client_id"],
            client_secret=app["client_secret"], client_auth=_CLIENT_AUTH)
    except oauth_flow.OAuthExchangeRefused as e:
        logger.info("notion : renouvellement refusé (%s)", entity_type)
        if "invalid_grant" in str(e):
            # Grant mort : MARQUER, jamais purger (`docs/connector-vault.md`) — la
            # fiche dit « à reconnecter » au lieu de ressembler à un jeton jamais posé.
            credentials_store.update_meta(entity_type, entity_id, CONNECTOR, account,
                                          {"health_ko": True, "health_reason": str(e)})
        raise NotionOAuthError(
            f"Notion refuse de renouveler la connexion — reconnecte Notion. ({e})") from e
    if not tokens.get("access_token"):
        raise NotionOAuthError("Notion n'a pas renvoyé de jeton au renouvellement.")
    neuf = {k: v for k, v in meta.items() if k not in ("refresh_token_enc", "expires_at")}
    neuf.update(_token_meta(entity_type, entity_id, tokens, now))
    if "refresh_token_enc" not in neuf:  # pas de rotation : l'ancien reste valable
        neuf["refresh_token_enc"] = meta["refresh_token_enc"]
    neuf["refreshed_at"] = now.isoformat()
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     tokens["access_token"], meta=neuf, account=account)
    return tokens["access_token"]
