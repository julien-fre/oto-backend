"""Acquisition du credential WordPress par l'écran d'autorisation NATIF du site.

WordPress (≥ 5.6) sert `wp-admin/authorize-application.php` : l'utilisateur,
connecté à son wp-admin, clique « Approuver », et WordPress CRÉE un mot de passe
d'application qu'il renvoie au `success_url` en query string (`site_url`,
`user_login`, `password`). Ce n'est pas de l'OAuth — pas de code à échanger —
mais la même forme de geste : un bouton, un consentement, un retour. Aucune
extension à installer, aucun mot de passe à copier.

Ce module porte le démarrage (`start`) et la fin (`finish`) ; le callback HTTP
vit dans `api/wordpress.py`. Le credential posé est EXACTEMENT celui du
formulaire (mêmes trois champs, même validation, même garde de compte), sous le
compte nommé par l'hôte du site — un compte = un site.

Garde-fous propres à ce flux :
- **state signé** (`oauth_flow.sign_state`, audience `wordpress`) portant le
  sub, l'org, le palier, le site DEMANDÉ et le front de retour. WordPress
  conserve les paramètres de `success_url` (`add_query_arg`), le state revient
  donc tel quel.
- **le site renvoyé doit être celui demandé** (même hôte) : un `site_url`
  substitué au retour ne pose jamais un credential ailleurs.
- **vérifié avant d'être posé** : `users/me` avec le mot de passe reçu. Un mot
  de passe d'application ne tourne pas (contrairement à un refresh token
  Salesforce), la sonde ne détruit donc rien.
- ⚠️ **le mot de passe arrive en query string** : `api/wordpress.py` retire la
  query du journal d'accès et de Sentry pour cette route. Il reste dans
  l'historique du navigateur de l'utilisateur — inhérent au protocole de
  WordPress ; le mot de passe est révocable depuis le profil WordPress.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urlsplit

from . import flow as oauth_flow

logger = logging.getLogger(__name__)

AUD = "wordpress"
CALLBACK_PATH = "/api/wordpress/connect/callback"
# `app_id` FIXE : WordPress range tous les mots de passe créés par ce flux sous
# le même identifiant d'application — l'utilisateur voit une seule entrée à
# révoquer, et un audit côté site peut les retrouver.
APP_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "oto-mcp/wordpress-connector"))
_SCOPES = ("member", "org", "group")


class ConnectRefused(ValueError):
    """Refus actionnable au démarrage — message sûr à afficher."""


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def account_for(site_url: str) -> str:
    """Le nom de compte d'un site : son hôte (+ chemin d'une install en
    sous-dossier). Stable d'une connexion à l'autre du même site."""
    parts = urlsplit(site_url)
    path = parts.path.strip("/")
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return f"{(parts.hostname or '').lower()}{port}{'/' + path if path else ''}"


def _app_name(org_id: int) -> str:
    """Le nom affiché sur l'écran d'autorisation — la marque du tenant de l'org
    (c'est elle que l'utilisateur connaît), repli sur celle de l'instance."""
    from .. import db, email_brand
    try:
        return email_brand.marque(db.org_tenant_slug(org_id)).nom
    except Exception:  # noqa: BLE001 — un nom d'écran ne bloque pas une connexion
        logger.debug("wordpress app_name: tenant brand unreadable", exc_info=True)
        return email_brand.nom_instance()


def _authorization_endpoint(site_url: str) -> str:
    """L'URL d'autorisation que le SITE annonce dans son index REST
    (`authentication.application-passwords.endpoints.authorization`) — jamais
    devinée : son absence dit que le site ne l'offre pas (HTTP sans
    environnement local, extension de sécurité, mots de passe d'application
    désactivés)."""
    from oto.tools.wordpress import WordPressClient

    # `public_index` part SANS en-tête Authorization : WordPress vérifie des
    # identifiants Basic sur TOUTE route REST, index compris — un identifiant
    # factice y rendrait un 401 (`rest_application_password_check_errors`). Les
    # deux valeurs du constructeur ne sont jamais envoyées.
    c = WordPressClient(site_url, "-", "-")
    try:
        index = c.public_index()
    except ValueError as e:
        raise ConnectRefused(str(e)) from e
    except Exception as e:  # noqa: BLE001 — toute panne réseau se dit à l'utilisateur
        raise ConnectRefused(
            f"{site_url} injoignable ({type(e).__name__}) — vérifie l'URL.") from e
    endpoint = (((index or {}).get("authentication") or {})
                .get("application-passwords") or {}).get("endpoints", {}).get("authorization")
    if not endpoint:
        raise ConnectRefused(
            "ce site n'offre pas l'autorisation d'application (mots de passe "
            "d'application désactivés, ou site en HTTP). Crée un mot de passe "
            "d'application dans ton profil WordPress et colle-le dans le formulaire.")
    return endpoint


def _saved_site(sub: str) -> str:
    """Reconnexion : le bouton de la fiche d'accès ne poste que les défauts du
    flux (pas de formulaire), donc pas de `site_url`. On reprend alors le site du
    credential déjà posé — celui que la cascade résout. Rien de posé ⟹ "" (et le
    refus nommé qui suit)."""
    from .. import access
    try:
        return access.resolve_credential("wordpress", want="byo", sub=sub,
                                         emit_on_failure=False).fields.get("site_url") or ""
    except Exception:  # noqa: BLE001 — rien de posé : l'appelant refuse nommément
        logger.debug("wordpress reconnect: no saved credential", exc_info=True)
        return ""


def start(ctx, values: dict):
    """`connector_flow` → URL d'autorisation du site. `values` : `site_url`
    (saisi), `scope` et `app` (posés par le front, hors formulaire)."""
    from .. import access, egress, roles
    from ..connectors import flow as connector_flow
    from oto.tools.wordpress import normalize_site_url

    raw = (values.get("site_url") or "").strip() or _saved_site(ctx.sub)
    if not raw:
        raise ConnectRefused("indique l'URL de ton site WordPress.")
    try:
        site = normalize_site_url(raw)
    except ValueError as e:
        raise ConnectRefused(str(e)) from e
    egress.check_url(site, connector="wordpress", field="site_url")

    scope = (values.get("scope") or "member").strip() or "member"
    if scope not in _SCOPES:
        raise ConnectRefused(f"palier inconnu : {scope!r}")
    org_id = access.current_org(ctx.sub)
    if org_id is None:
        raise ConnectRefused("aucune org de contexte — reconnecte-toi et réessaie.")
    group_id: Optional[int] = None
    if scope == "org" and not roles.is_org_admin(ctx.sub, org_id):
        raise PermissionError("seul un admin de l'org peut connecter un site pour toute l'org.")
    if scope == "group":
        group_id = access.current_group(ctx.sub)
        if group_id is None or not roles.can_admin_group(ctx.sub, group_id):
            raise PermissionError("seul un chef d'équipe peut connecter un site pour l'équipe.")

    endpoint = _authorization_endpoint(site)
    # L'écran d'autorisation est servi par le site lui-même : son hôte est
    # celui du site (un site qui annonce une autorisation AILLEURS est refusé).
    if _host(endpoint) != _host(site):
        raise ConnectRefused(
            f"le site annonce une page d'autorisation sur un autre hôte "
            f"({_host(endpoint)}) — refusé.")

    payload = {"sub": ctx.sub, "org": org_id, "scope": scope, "site": site,
               "app": oauth_flow.resolve_return_app(values.get("app"))}
    if group_id is not None:
        payload["group"] = group_id
    state = oauth_flow.sign_state(AUD, payload)
    callback = oauth_flow.redirect_uri(CALLBACK_PATH)
    success = f"{callback}?{urlencode({'state': state})}"
    reject = f"{callback}?{urlencode({'state': state, 'success': 'false'})}"
    params = {"app_name": _app_name(org_id), "app_id": APP_ID,
              "success_url": success, "reject_url": reject}
    sep = "&" if "?" in endpoint else "?"
    return connector_flow.FlowStart(auth_url=f"{endpoint}{sep}{urlencode(params)}",
                                    details={"site": site, "account": account_for(site)})


def read_state(state: Optional[str]) -> Optional[dict]:
    data = oauth_flow.read_state(AUD, state)
    if not data:
        return None
    if (not isinstance(data.get("sub"), str) or not isinstance(data.get("org"), int)
            or data.get("scope") not in _SCOPES or not isinstance(data.get("site"), str)):
        return None
    if data["scope"] == "group" and not isinstance(data.get("group"), int):
        return None
    if not isinstance(data.get("app"), str):
        data["app"] = ""
    return data


def finish(parsed: dict, site_url: str, user_login: str, password: str) -> str:
    """Vérifie puis pose le credential. Synchrone (réseau + SQL) — l'appelant
    l'exécute hors boucle. Rend le nom de compte posé."""
    from .. import credentials_store, egress
    from oto.tools.wordpress import WordPressClient, normalize_site_url

    asked = parsed["site"]
    returned = normalize_site_url(site_url or asked)
    if _host(returned) != _host(asked):
        raise ConnectRefused("le site renvoyé ne correspond pas au site demandé.")
    if not user_login or not password:
        raise ConnectRefused("WordPress n'a renvoyé ni identifiant ni mot de passe.")
    egress.check_url(asked, connector="wordpress", field="site_url")

    me = WordPressClient(asked, user_login, password).me()
    if not me.get("id"):
        raise ConnectRefused("le mot de passe reçu n'authentifie pas (users/me vide).")

    fields = credentials_store.validate_fields("wordpress", {
        "site_url": asked, "username": user_login, "application_password": password})
    secret = credentials_store.pack_secret("wordpress", fields)
    meta = {"verified_at": datetime.now(timezone.utc).isoformat(),
            "connected_via": "authorize_application",
            **credentials_store.meta_fields("wordpress", fields)}
    account = account_for(asked)
    sub, org_id, scope = parsed["sub"], parsed["org"], parsed["scope"]

    if scope == "org":
        from .. import org_store
        credentials_store.guard_account_write(credentials_store.ORG, str(org_id),
                                              "wordpress", account, org=org_id)
        org_store.set_org_secret(org_id, "wordpress", secret, set_by=sub, meta=meta,
                                 account=account)
    elif scope == "group":
        from .. import group_store
        gid = parsed["group"]
        credentials_store.guard_account_write("group", str(gid), "wordpress", account,
                                              org=org_id)
        group_store.set_group_secret(gid, "wordpress", secret, set_by=sub, meta=meta,
                                     account=account)
    else:
        eid = credentials_store.member_id(org_id, sub)
        credentials_store.guard_account_write(credentials_store.MEMBER, eid, "wordpress",
                                              account, org=org_id)
        credentials_store.set_credential(credentials_store.MEMBER, eid, "wordpress", secret,
                                         set_by=sub, account=account, meta=meta)
    return account
