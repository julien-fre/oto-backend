"""Acquisition OAuth2 Zoho en **Server-based** — le second mode de connexion.

Les deux modes coexistent, et c'est ce qui rend l'ajout peu invasif : ils
produisent **exactement le même credential** (`client_id` + `client_secret` +
`refresh_token` + `data_center`). Seule la façon de l'OBTENIR change.

- **Self Client** (existant, inchangé) : l'utilisateur génère les valeurs dans la
  console Zoho et les colle dans le formulaire. Aucun code dédié — c'est le
  formulaire générique des credentials.
- **Server-based** (ce module) : l'utilisateur clique « Connecter », consent chez
  Zoho, et revient connecté. Rien à copier.

Pourquoi l'ajouter — trois incidents de la même famille (#190 `zoho_modules`
OAUTH_SCOPE_MISMATCH, #202 Analytics INVALID_OAUTHSCOPE, et le Desk limité à
`Desk.articles.READ` qui a supprimé la recherche native) : en Self Client, c'est
**l'utilisateur** qui choisit les scopes à la main, et il se trompe — sans qu'on
puisse rien corriger côté serveur. Ici **c'est oto qui les déclare** dans l'URL
d'autorisation. Bénéfice second : plus aucun secret n'a besoin de circuler par
mail (vécu 28/07, un `client_secret` reçu en clair).

**D'où vient l'app ?** Du **coffre**, comme n'importe quel credential — jamais de
l'environnement. `client_id`/`client_secret` sont posés sur la carte du connecteur
par l'user, l'équipe, l'org ou la plateforme, et résolus par la **cascade
habituelle** (`access.resolve_credential_fields` : membre > équipe > org >
plateforme). Conséquences : chaque org peut apporter la sienne, une clé
**plateforme** posée par Otomata donne le « un clic » à tout le monde sans rien
changer au code, et le partage/la gouvernance existants s'appliquent tels quels.

La connexion est donc en **deux temps** (patron `status_hints` déjà en place pour
unipile/sessions) : (1) poser l'app — `client_id` + `client_secret` + région ;
(2) consentir, ce qui remplit le `refresh_token`. D'où un `refresh_token`
FACULTATIF sur la carte : en server-based il n'est pas collé, il est obtenu.

Le transport OAuth (state signé, échange du code, URI de redirection) est délégué
à **`oauth_flow`**, la fabrique commune — ce module ne garde que ce qui est propre
à Zoho : régions, scopes par connecteur, origine de l'app, rangement du credential.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Optional

from mcp.shared.exceptions import McpError

from .. import access, credentials_store, providers
from . import flow as oauth_flow

logger = logging.getLogger(__name__)

_STATE_TTL = 600  # 10 min — le temps de lire un écran de consentement

# Régions Zoho : l'app OAuth et le refresh token sont liés à LEUR data center
# (un client `.eu` sur `accounts.zoho.com` = `invalid_client` opaque).
_ACCOUNTS = {
    "com": "https://accounts.zoho.com",
    "eu": "https://accounts.zoho.eu",
    "in": "https://accounts.zoho.in",
    "au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zohocloud.ca",
}

# Domaine d'API Analytics par région — même découpage que `_ACCOUNTS`, autre hôte.
_ANALYTICS_DOMAINS = {
    "com": "https://analyticsapi.zoho.com",
    "eu": "https://analyticsapi.zoho.eu",
    "in": "https://analyticsapi.zoho.in",
    "au": "https://analyticsapi.zoho.com.au",
    "jp": "https://analyticsapi.zoho.jp",
    "ca": "https://analyticsapi.zohocloud.ca",
}

# Scopes demandés PAR CONNECTEUR — c'est tout l'intérêt du mode server-based :
# ils ne dépendent plus de ce que l'utilisateur a coché.
SCOPES = {
    "zoho": ("ZohoCRM.modules.ALL", "ZohoCRM.settings.modules.READ",
             "ZohoCRM.settings.fields.READ", "ZohoCRM.users.READ"),
    "zohodesk": ("Desk.articles.READ", "Desk.basic.READ", "Desk.search.READ",
                 "Desk.tickets.READ", "Desk.contacts.READ"),
    "zohoanalytics": ("ZohoAnalytics.data.read", "ZohoAnalytics.metadata.read"),
}

CONNECTORS = tuple(SCOPES)

# Les champs que le consentement PRODUIT (cf. `persist`) — le reste des champs requis
# d'un connecteur doit être saisi à la main. C'est ce qui sépare « pas encore autorisé »
# de « l'autorisation ne suffira pas » : Analytics exige un `org_id` qu'aucun flux OAuth
# ne peut deviner. Gardé ici, à côté de `persist`, pour que les deux ne divergent pas
# (tripwire `test_editor_app.py`).
PERSISTED_FIELDS = ("client_id", "client_secret", "refresh_token", "data_center")


class ZohoOAuthError(ValueError):
    """Échec d'acquisition — message SANS secret (cf. `zoho.auth`, incident #284)."""


# --- app (client_id / client_secret) ----------------------------------------

def editor_app(connector: str, data_center: str) -> dict:
    """L'app de l'ÉDITEUR (oto) pour cette région, ou `{}`.

    C'est le cran qui rend la connexion « un clic » sans que personne n'ait à créer
    d'app : oto publie la sienne, l'utilisateur ne fait que consentir. Elle ne donne
    accès à rien par elle-même — cf. l'invariant dans `credentials_store` §app
    d'éditeur. `{}` si oto n'en publie pas pour cette région : le Self Client reste
    alors la voie, et c'est `resolve_app` qui le dit."""
    if not (data_center or "").strip():
        return {}
    return credentials_store.get_editor_app(connector, data_center) or {}


def app_fields(connector: str, sub: str, data_center: str = "") -> dict:
    """Champs de l'app à utiliser pour ce (sub, connecteur, région).

    Deux origines, dans cet ordre — **l'app apportée prime toujours sur la nôtre** :
    1. le credential BYO résolu (membre > équipe > org) : une org qui veut voir SON
       app dans ses logs Zoho la pose, et rien ne change pour elle ;
    2. à défaut, l'app d'ÉDITEUR de la région (`data_center`), si oto en publie une.

    `{}` si ni l'une ni l'autre : c'est l'état NOMINAL d'une première connexion sur un
    connecteur sans app d'éditeur, jamais une erreur."""
    # ⚠️ `resolve_credential(..., sub=…)` et NON `resolve_credential_fields`, qui
    # n'a pas de paramètre `sub` : on est dans une route REST, hors contexte MCP, où
    # le sub ambiant n'existe pas. `emit_on_failure=False` — c'est une SONDE qui avale
    # l'échec, elle ne doit pas polluer le signal d'usage (ADR 0017).
    #
    # ⚠️ On n'attrape QUE la `McpError` de la cascade — c'est elle, et elle seule,
    # qui dit « pas encore de credential ». Attraper `Exception` faisait basculer
    # SILENCIEUSEMENT sur l'app d'éditeur oto au moindre hoquet de coffre
    # (déchiffrement, DB) : l'org qui a posé SON app pour la voir dans ses logs Zoho
    # ne la voyait plus, et rien ne le disait (inventaire des silences du 2026-08-27,
    # site B7). Une erreur de coffre est un refus, pas une absence.
    try:
        byo = access.resolve_credential(
            connector, want="byo", sub=sub, emit_on_failure=False).fields or {}
    except McpError:  # état NOMINAL : aucun credential apporté à ce niveau
        byo = {}
    if byo.get("client_id") and byo.get("client_secret"):
        return byo
    return editor_app(connector, data_center)


def resolve_app(fields: Optional[dict]) -> tuple[str, str]:
    """`(client_id, client_secret)` de l'app à utiliser, pris dans le credential
    résolu. Lève un message actionnable si l'app n'a pas encore été posée."""
    cid = (fields or {}).get("client_id")
    sec = (fields or {}).get("client_secret")
    if cid and sec:
        return cid, sec
    raise ZohoOAuthError(
        "Aucune app OAuth Zoho disponible pour cette région. Renseigne « client id » "
        "et « client secret » d'un self client Zoho sur la carte du connecteur (ou "
        "demande à ton org de partager le sien), puis relance la connexion.")


def has_app(connector: str, sub: str, data_center: str = "") -> bool:
    """Une app est-elle à disposition ? Avec `data_center`, la réponse vaut POUR cette
    région ; sans, elle vaut « une app existe quelque part » — l'app apportée par le
    membre/l'équipe/l'org, ou une app d'éditeur pour au moins une région (le front
    affiche le bouton, la région n'étant choisie qu'au clic)."""
    if data_center:
        f = app_fields(connector, sub, data_center)
        return bool(f.get("client_id") and f.get("client_secret"))
    # Sans région, `app_fields` ne consulte QUE le BYO (le repli éditeur est keyé par
    # région) — d'où la seconde question, posée à part.
    byo = app_fields(connector, sub)
    if byo.get("client_id") and byo.get("client_secret"):
        return True
    return _has_editor_app(connector)


def _has_editor_app(connector: str) -> bool:
    """oto publie-t-il une app pour ce connecteur, dans n'importe quelle région ?
    Fail-open : une panne de lecture ne doit pas cacher le bouton (au pire, `start`
    rendra un message actionnable)."""
    try:
        return bool(credentials_store.list_editor_apps(connector))
    # noqa: SILENT — fail-open : une panne de lecture ne doit pas cacher le bouton
    except Exception:  # noqa: BLE001
        return False


# --- state signé (délégué à la fabrique) --------------------------------------
#
# `oauth_flow` porte la mécanique — HMAC, base64url, TTL — et LIE le state à son
# audience, ce que les implémentations séparées ne faisaient pas : elles signaient
# toutes avec le même secret, au même format, sans discriminant, si bien qu'un
# state d'un flux était structurellement acceptable par le callback d'un autre.

_AUDIENCE = "zoho"


def redirect_uri() -> str:
    """UNE seule URI pour les trois connecteurs Zoho (le connecteur voyage dans le
    `state`) : une URI s'enregistre au byte près côté Zoho — une seule à déclarer
    par app au lieu de trois."""
    return oauth_flow.redirect_uri("/api/zoho/oauth/callback")


def make_state(sub: str, org_id: int, connector: str, data_center: str,
               return_app: str = "") -> str:
    """`return_app` = le FRONT qui a demandé le consentement. Il DOIT voyager dans le
    state signé : le callback est appelé par Zoho, sans session, donc c'est la seule
    mémoire du demandeur qui survit à l'aller-retour. Déjà résolu contre la liste
    fermée par l'appelant (`oauth_flow.resolve_return_app`) — on ne signe jamais une
    valeur de client brute."""
    return oauth_flow.sign_state(_AUDIENCE, {"sub": sub, "org": org_id,
                                             "c": connector, "dc": data_center,
                                             "a": return_app})


def verify_state(state: str) -> Optional[dict]:
    """`{sub, org, connector, data_center}` si le state est valide, de CE flux, et
    non expiré. Les invariants métier (connecteur connu, région connue) restent
    ici — la fabrique ne connaît que le transport."""
    d = oauth_flow.read_state(_AUDIENCE, state, ttl=_STATE_TTL)
    if not d:
        return None
    if d.get("c") not in CONNECTORS or d.get("dc") not in _ACCOUNTS:
        return None
    if not isinstance(d.get("sub"), str) or not isinstance(d.get("org"), int):
        return None
    # `a` absent = state signé AVANT ce champ et encore vivant dans la fenêtre du
    # TTL : on dégrade vers le défaut historique plutôt que de rejeter un
    # consentement en cours (même tolérance que salesforce).
    return {"sub": d["sub"], "org": d["org"], "connector": d["c"],
            "data_center": d["dc"], "return_app": d.get("a") or ""}


# --- flux --------------------------------------------------------------------

def build_auth_url(sub: str, org_id: int, connector: str, data_center: str,
                   app: Optional[dict] = None, return_app: str = "") -> str:
    """URL de consentement Zoho. `access_type=offline` + `prompt=consent` sont
    REQUIS pour obtenir un refresh_token (sans eux Zoho ne renvoie qu'un access
    token d'une heure, et la connexion meurt silencieusement au bout d'une heure)."""
    if connector not in SCOPES:
        raise ZohoOAuthError(f"Connecteur Zoho inconnu : {connector}")
    dc = (data_center or "").strip().lower()
    if dc not in _ACCOUNTS:
        raise ZohoOAuthError(
            f"Data center Zoho non reconnu : {data_center!r} — l'un de "
            f"{', '.join(_ACCOUNTS)}.")
    client_id, _ = resolve_app(app if app is not None else app_fields(connector, sub, dc))
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "scope": ",".join(SCOPES[connector]),
        "redirect_uri": redirect_uri(),
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(sub, org_id, connector, dc, return_app),
    })
    return f"{_ACCOUNTS[dc]}/oauth/v2/auth?{q}"


def exchange_code(code: str, data_center: str,
                  app: Optional[dict] = None) -> dict:
    """Code éphémère → tokens, via la fabrique (corps form-encodé, erreurs rédigées).
    Ne restent ici que les spécificités Zoho : le domaine régional et le diagnostic
    du refresh_token absent."""
    client_id, client_secret = resolve_app(app)
    try:
        payload = oauth_flow.exchange_code(
            f"{_ACCOUNTS[data_center]}/oauth/v2/token",
            code=code, client_id=client_id, client_secret=client_secret,
            redirect=redirect_uri())
    except oauth_flow.OAuthFlowError as e:
        msg = str(e)
        if "code" in msg.lower():
            msg += (" Le code d'autorisation expire en quelques minutes — relance "
                    "la connexion.")
        raise ZohoOAuthError(msg)
    if not payload.get("refresh_token"):
        raise ZohoOAuthError(
            "Zoho n'a pas renvoyé de refresh_token : l'autorisation a déjà été "
            "accordée à cette app. Révoque-la dans ton compte Zoho "
            "(Sécurité → Applications connectées) puis relance la connexion.")
    return payload


def analytics_orgs(fields: dict) -> list[dict]:
    """Organisations Analytics visibles par ce credential (`org_id`, `name`, `role`).

    Analytics est le seul des trois à exiger une organisation sur CHAQUE appel (le CRM
    la déduit du jeton, Desk sait s'en passer en mono-org). Elle est donc à renseigner
    — mais l'API sait la dire, ce qui évite d'envoyer l'utilisateur chercher un
    identifiant à onze chiffres dans l'interface Zoho."""
    from oto.tools.zohoanalytics.client import ZohoAnalyticsClient
    dc = (fields.get("data_center") or "").strip().lower()
    if dc not in _ACCOUNTS:
        raise ZohoOAuthError(f"Data center Zoho non reconnu : {fields.get('data_center')!r}.")
    return ZohoAnalyticsClient(
        client_id=fields.get("client_id"), client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"), org_id=None,
        api_domain=_ANALYTICS_DOMAINS[dc], accounts_url=_ACCOUNTS[dc],
    ).list_orgs()


def _derived_fields(connector: str, fields: dict) -> dict:
    """Ce que le consentement permet de DÉDUIRE, quand il n'y a qu'une réponse possible.

    Analytics : une seule organisation ⟹ on la pose, l'utilisateur ne voit jamais la
    question. PLUSIEURS ⟹ on ne devine pas. La réponse de Zoho ne désigne aucune
    organisation par défaut, et sur le premier compte réel testé il y en avait deux —
    « la première » aurait été la mauvaise. Le champ reste alors vide : l'état du
    credential le signale, et l'utilisateur choisit sur des noms.

    Best-effort : un échec ici ne doit JAMAIS faire perdre un consentement réussi —
    l'utilisateur retomberait sur la saisie manuelle, pas sur une erreur."""
    if connector != "zohoanalytics":
        return {}
    try:
        orgs = analytics_orgs(fields)
    except Exception:  # noqa: BLE001
        logger.debug("analytics org discovery failed", exc_info=True)
        return {}
    return {"org_id": orgs[0]["org_id"]} if len(orgs) == 1 else {}


def persist(sub: str, org_id: int, connector: str, data_center: str,
            tokens: dict, app: Optional[dict] = None, *,
            entity_type: str = "member") -> None:
    """Range le credential SOUS LA MÊME FORME que le mode Self Client — c'est ce
    qui permet aux deux modes de coexister sans toucher au client ni à la
    résolution. `entity_type='member'` = clé scopée (sub, org) (ADR 0033)."""
    client_id, client_secret = resolve_app(app)
    fields = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "data_center": data_center,
    }
    # ⚠️ Après les champs du flux, jamais avant : la découverte a besoin du jeton
    # tout juste obtenu. Et hors de `PERSISTED_FIELDS` à dessein — un `org_id` qu'on
    # n'a PAS pu déduire doit rester signalé comme manquant.
    fields.update(_derived_fields(connector, fields))
    secret = credentials_store.secret_from_input(connector, None, fields)
    # ⚠️ TOUJOURS via `member_id(org, sub)` — l'ordre est (org, sub), et l'**AAD de
    # chiffrement en dérive** : un id reconstruit à la main dans le mauvais ordre ne
    # produit pas un credential « mal rangé » mais un credential INDÉCHIFFRABLE, que
    # la cascade ne verra jamais (vécu au premier consentement réel, 28/07).
    entity_id = (credentials_store.member_id(org_id, sub)
                 if entity_type == "member" else str(org_id))
    credentials_store.set_credential(
        entity_type, entity_id, connector, secret, set_by=sub,
        meta={"acquired_via": "oauth"})


def supports(connector: str) -> bool:
    """Le connecteur propose-t-il le mode server-based ? (le front gate le bouton)"""
    return connector in SCOPES and connector in providers.REGISTRY
