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
- **state à usage unique** (`jti`, `oauth_flow.consume_state`) : la
  `success_url` traverse le site, ses extensions et ses journaux ; rejouée avec
  un autre identifiant du même hôte, elle écraserait le credential.
- **droits re-vérifiés au retour** (`still_allowed`) : le state vit 10 min, un
  admin qui perd son rôle entre-temps ne pose plus rien pour l'org (ADR 0038,
  comme Salesforce).
- **HTTPS seulement**, sauf destination interne déclarée par l'opérateur
  (`check_site`) : le mot de passe part en HTTP Basic.
- **le site renvoyé doit être celui demandé** (même hôte) : un `site_url`
  substitué au retour ne pose jamais un credential ailleurs.
- **vérifié avant d'être posé** : `users/me` avec le mot de passe reçu. Un mot
  de passe d'application ne tourne pas (contrairement à un refresh token
  Salesforce), la sonde ne détruit donc rien.
- ⚠️ **le mot de passe arrive en query string** : la query de cette route est
  retirée du journal d'accès (`journal_secrets`) et de Sentry (`sentry_setup`). Il reste dans
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


def check_site(site_url: str) -> bool:
    """La garde de TOUT chemin qui envoie le mot de passe au site : egress, puis
    HTTPS. Le mot de passe d'application part en HTTP Basic — en clair sur `http`.
    Seule exception : une destination interne DÉCLARÉE par l'opérateur
    (`OTO_EGRESS_ALLOW`, un WordPress local de développement), la même liste
    nommée que la garde d'egress — jamais un réglage par connecteur.

    Rend `allow_http` à passer au client (qui refuse `http` par défaut) : True
    seulement pour cette exception déclarée."""
    from .. import egress

    egress.check_url(site_url, connector="wordpress", field="site_url")
    return http_allowed(site_url)


def http_allowed(site_url: str) -> bool:
    """La moitié « schéma » de `check_site`, pour un appelant qui pose lui-même la
    garde d'egress (les outils, que `tests/test_egress_guard.py` lit) : False en
    HTTPS, True pour une destination interne déclarée en HTTP, refus sinon."""
    from .. import egress

    parts = urlsplit((site_url or "").strip())
    if parts.scheme == "https":
        return False
    port = parts.port or 80
    declared = egress.declared_exceptions()
    if any((a, port) in declared
           for a in egress.resolved_addresses(parts.hostname or "", port)):
        return True
    raise ConnectRefused(
        f"{site_url} est en HTTP : le mot de passe d'application y partirait en "
        "clair. Indique l'adresse en https:// (WordPress refuse d'ailleurs ces "
        "mots de passe hors HTTPS, sauf environnement local).")


def still_allowed(parsed: dict) -> bool:
    """Le droit d'écrire au palier du state, relu AU RETOUR. Synchrone (SQL)."""
    from .. import roles

    if parsed["scope"] == "org":
        return roles.is_org_admin(parsed["sub"], parsed["org"])
    if parsed["scope"] == "group":
        return roles.can_admin_group(parsed["sub"], parsed["group"])
    return True


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
        logger.warning("wordpress app_name: tenant brand unreadable for org %s, "
                       "instance name shown", org_id, exc_info=True)
        return email_brand.nom_instance()


def _authorization_endpoint(site_url: str, *, allow_http: bool = False) -> str:
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
    c = WordPressClient(site_url, "-", "-", allow_http=allow_http)
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
    refus nommé qui suit) ; tout autre refus (plusieurs sites, accès) est dit tel
    quel, une panne remonte."""
    from .. import access
    from ..mcp_errors import McpError
    try:
        return access.resolve_credential("wordpress", want="byo", sub=sub,
                                         emit_on_failure=False,
                                         check_usage=False).fields.get("site_url") or ""
    except access.CredentialUnavailable:
        return ""
    except McpError as e:
        raise ConnectRefused(e.error.message) from e


def start(ctx, values: dict):
    """`connector_flow` → URL d'autorisation du site. `values` : `site_url`
    (saisi), `scope` et `app` (posés par le front, hors formulaire)."""
    from .. import access, roles
    from ..connectors import flow as connector_flow
    from oto.tools.wordpress import normalize_site_url

    raw = (values.get("site_url") or "").strip() or _saved_site(ctx.sub)
    if not raw:
        raise ConnectRefused("indique l'URL de ton site WordPress.")
    try:
        # La FORME seule ici (http compris) : `check_site` décide ensuite du schéma,
        # avec l'exception déclarée qu'elle seule connaît.
        site = normalize_site_url(raw, allow_http=True)
    except ValueError as e:
        raise ConnectRefused(str(e)) from e
    allow_http = check_site(site)

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

    endpoint = _authorization_endpoint(site, allow_http=allow_http)
    # L'écran d'autorisation est servi par le site lui-même : son hôte est
    # celui du site (un site qui annonce une autorisation AILLEURS est refusé).
    if _host(endpoint) != _host(site):
        raise ConnectRefused(
            f"le site annonce une page d'autorisation sur un autre hôte "
            f"({_host(endpoint)}) — refusé.")

    payload = {"sub": ctx.sub, "org": org_id, "scope": scope, "site": site,
               "app": oauth_flow.resolve_return_app(values.get("app")),
               "jti": oauth_flow.new_jti()}
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
            or data.get("scope") not in _SCOPES or not isinstance(data.get("site"), str)
            or not isinstance(data.get("jti"), str)):
        return None
    if data["scope"] == "group" and not isinstance(data.get("group"), int):
        return None
    if not isinstance(data.get("app"), str):
        data["app"] = ""
    return data


def finish(parsed: dict, site_url: str, user_login: str, password: str) -> str:
    """Vérifie puis pose le credential. Synchrone (réseau + SQL) — l'appelant
    l'exécute hors boucle. Rend le nom de compte posé."""
    from .. import credentials_store
    from oto.tools.wordpress import WordPressClient, normalize_site_url

    asked = parsed["site"]
    returned = normalize_site_url(site_url or asked, allow_http=True)
    if _host(returned) != _host(asked):
        raise ConnectRefused("le site renvoyé ne correspond pas au site demandé.")
    if not user_login or not password:
        raise ConnectRefused("WordPress n'a renvoyé ni identifiant ni mot de passe.")
    allow_http = check_site(asked)

    me = WordPressClient(asked, user_login, password, allow_http=allow_http).me()
    if not me.get("id"):
        raise ConnectRefused("le mot de passe reçu n'authentifie pas (users/me vide).")

    fields = credentials_store.validate_fields("wordpress", {
        "site_url": asked, "username": user_login, "application_password": password})
    secret = credentials_store.pack_secret("wordpress", fields)
    meta = {"verified_at": datetime.now(timezone.utc).isoformat(),
            "connected_via": "authorize_application",
            **credentials_store.meta_fields("wordpress", fields)}
    account = account_for(asked)
    sub, org_id = parsed["sub"], parsed["org"]
    etype, eid = credentials_store.entity_for_scope(parsed["scope"], org_id, sub,
                                                    parsed.get("group"))
    credentials_store.guard_account_write(etype, eid, "wordpress", account, org=org_id)
    credentials_store.set_credential(etype, eid, "wordpress", secret, set_by=sub,
                                     account=account, meta=meta)
    return account
