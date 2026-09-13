"""Le relais d'autorisation : l'émetteur ANNONCÉ devient celui qui ESTAMPILLE la réponse
(RFC 9207).

**Le défaut mesuré (08/09/2026).** La façade s'annonce serveur d'autorisation (`issuer` = le
host, RFC 8414 §3.3), mais la réponse d'autorisation sortait de Logto, estampillée `iss` =
SON émetteur. Le SDK MCP Python 2.0 compare cet `iss` à l'`issuer` découvert et refuse le
flux juste après la connexion (« Authorization response iss mismatch »), avant tout jeton —
deux fois en production, sur le host d'un tenant. §2.4 impose la validation dès que `iss`
est PRÉSENT : ne rien annoncer ne protège de rien.

**Ce que fait le relais.** Sur un host DÉCLARÉ (`relais_actif`), la métadonnée annonce
`/oauth/relay/authorize` et `/oauth/token` :
- l'autorisation réécrit DEUX segments de la requête — `redirect_uri` devient le rappel de
  la façade (`<as_base>/oauth/callback`, UN rappel posé sur l'application partagée) et
  `state` scelle le rappel et le `state` du client (`relay_seals`). Le reste, PKCE compris,
  arrive chez Logto à l'octet près ;
- le retour vérifie le sceau et l'`iss` de Logto, puis renvoie le client sur SON rappel,
  paramètres de réponse REMPLACÉS, avec `iss` = l'`issuer` annoncé ;
- l'échange de jeton remet le rappel de la façade pour un code MARQUÉ et transmet le reste
  tel quel. Logto émet et signe comme avant ; rien n'est stocké.

**Ce qui ne change pas, et pourquoi.** Un host non déclaré sert la métadonnée d'avant, à
l'octet près ; `/oauth/authorize` (oto#202) garde son comportement — un client qui a lu la
métadonnée d'avant (autorisation là, jeton chez Logto) ne recevra jamais un code relayé que
Logto refuserait. `authorization_response_iss_parameter_supported` n'est PAS annoncé : le
trajet direct reste possible (repli), et un client qui exige `iss` sur la foi de ce drapeau
(ChatGPT/Codex, le parcours tableau de bord de certains agents) casserait là où il marche.
⚠️ **Une fois un host déclaré, ces trois routes ne se retirent plus** : un client garde la
métadonnée qu'il a lue. Retirer la déclaration arrête d'annoncer et de relayer ; revenir à
une version antérieure à ce module casserait ces clients.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, quote, unquote_plus, urlencode, urlparse, urlsplit, urlunsplit

import httpx
from pydantic import AnyHttpUrl
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import facade
from .authorize_consent import redirection
from .relay_seals import (CODE_MARQUE, lire_code, marquer_code, ouvrir_etat, sceller_etat,
                          secret)

_log = logging.getLogger("oto_mcp.oauth_facade")

AUTHORIZE_PATH = "/oauth/relay/authorize"
CALLBACK_PATH = "/oauth/callback"
TOKEN_PATH = "/oauth/token"
_HOSTS_ENV = "OTO_MCP_OAUTH_RELAY_HOSTS"
# ≥ au délai d'un client : si le relais abandonnait le premier, Logto consommerait quand
# même le code (ou ferait tourner le jeton de rafraîchissement), et le rejeu du client
# ferait RÉVOQUER toute la délégation. Jamais de nouvel essai côté relais.
_JETON_TIMEOUT = 30.0
_CORPS_MAX = 64 * 1024
_GRANTS = frozenset({"authorization_code", "refresh_token"})
_CLES_DE_REPONSE = frozenset({"code", "state", "iss", "error", "error_description", "error_uri"})
# Deux freins sur l'échange de jeton, sans lesquels n'importe qui ferait frapper Logto
# depuis l'IP de la box : un seau par IP OBSERVÉE (large à dessein — un client hébergé
# rafraîchit tous ses utilisateurs depuis quelques IP) et un plafond d'échanges en vol,
# qui garde le client HTTP partagé disponible pour les vrais rafraîchissements.
_SEAU_PAR_MIN, _SEAU_RAFALE, _SEAUX_MAX = 120.0, 60.0, 50_000
_EN_VOL_MAX = 32
_seaux: dict = {}
_en_vol = 0
_sans_secret_signale: set = set()


def hosts_declares() -> frozenset:
    raw = os.environ.get(_HOSTS_ENV, "")
    return frozenset(h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip())


def relais_actif(host: str) -> bool:
    """Le host est déclaré ET le process peut sceller. Une déclaration sans secret ne
    s'annonce pas (sinon chaque autorisation se replierait sous une métadonnée qui promet
    le relais) — et le dit, une fois par host."""
    host = (host or "").lower().rstrip(".")
    if host not in hosts_declares():
        return False
    if secret() is None:
        if host not in _sans_secret_signale:
            _sans_secret_signale.add(host)
            _log.error("relais d'autorisation : %s est déclaré dans %s mais "
                       "OTO_MCP_OAUTH_STATE_SECRET est absent — non annoncé, non relayé",
                       host, _HOSTS_ENV)
        return False
    return True


@dataclass(frozen=True)
class Cible:
    """L'annuaire servi sur un host — résolu côté serveur, jamais lu dans la requête."""
    host: str             # le host qui décide de la déclaration (celui de l'issuer)
    as_base: str
    app_id: str
    oidc_public: str      # où le NAVIGATEUR se connecte
    oidc_jeton: str       # où part l'échange de jeton, de serveur à serveur
    emetteurs: frozenset  # les `iss` que cet annuaire peut estampiller
    consentement: bool    # oto#202 : pour NOTRE annuaire seulement
    directory: object = None
    label: str = "?"

    @property
    def rappel(self) -> str:
        return f"{self.as_base}{CALLBACK_PATH}"

    @property
    def issuer(self) -> str:
        return str(AnyHttpUrl(self.as_base))   # la normalisation de `facade.as_metadata`


def cible_pour_host(host: str, public_url: str, claude_app_id: str) -> Cible:
    public_url = public_url.rstrip("/")
    entry = facade.tenant_for_host(host)
    if entry is None:
        d = facade._primary_directory()
        alt = os.environ.get("LOGTO_ENDPOINT_ALT", "").strip().rstrip("/")
        emetteurs = {facade._logto_issuer(), facade._logto_public_oidc()}
        emetteurs |= {f"{alt}/oidc"} if alt else set()
        # Le jeton part vers l'ORIGINE (`LOGTO_ENDPOINT`), pas le domaine public : même
        # annuaire, même jeton, sans le pare-feu applicatif devant le domaine public.
        return Cible(urlparse(public_url).hostname or "", public_url, claude_app_id,
                     facade._logto_public_oidc(), facade._logto_issuer(),
                     frozenset(e.rstrip("/") for e in emetteurs), True,
                     d if facade._credential_present(d) else None, d.label)
    d = facade.directory_for_tenant(entry)
    oidc = entry.issuer.rstrip("/")
    return Cible(host, f"https://{host}", entry.oauth_client_id or "", oidc, oidc,
                 frozenset({oidc}), False,
                 d if d is not None and facade._credential_present(d) else None, entry.slug)


# ── la requête d'autorisation ─────────────────────────────────────────────────

def _cle(segment: str) -> str:
    return unquote_plus(segment.split("=", 1)[0])


def lire_demande(requete: str) -> tuple[Optional[dict], str]:
    """`(demande, "")` si la requête se relaie, `(None, raison)` sinon.

    ⚠️ PKCE S256 est EXIGÉ, pas seulement transmis : c'est la seule garde réelle contre un
    code intercepté (la marque ne lie qu'un rappel). Et seul le mode `query` se relaie : en
    `form_post` Logto POSTerait au rappel de la façade, en `fragment` le code n'atteindrait
    jamais le serveur."""
    paires = parse_qsl(requete, keep_blank_values=True)
    cles = [k for k, _ in paires]
    if "request" in cles or "request_uri" in cles:
        return None, "request_object"
    uniques = ("client_id", "redirect_uri", "response_type", "code_challenge",
               "code_challenge_method")
    if any(cles.count(k) != 1 for k in uniques) or cles.count("state") > 1 \
            or cles.count("response_mode") > 1:
        return None, "param_shape"
    v = dict(paires)
    if v["response_type"] != "code" or v.get("response_mode", "query") != "query":
        return None, "response_mode"
    if v["code_challenge_method"] != "S256" or not v["code_challenge"]:
        return None, "pkce"
    return {"client_id": v["client_id"], "redirect_uri": v["redirect_uri"],
            "state": v.get("state")}, ""


def reecrire(requete: str, remplacements: dict, ajouter: Optional[dict] = None) -> str:
    """La chaîne urlencodée avec les segments nommés remplacés — ajoutés s'ils manquent
    et figurent dans `ajouter`. ⚠️ Segment par segment, jamais re-sérialisée : `urlencode`
    réécrirait l'encodage du reste, et une paire répétée (`resource`) doit survivre."""
    segments, vus = [], set()
    for s in (requete.split("&") if requete else []):
        cle = _cle(s) if s else ""
        if cle in remplacements:
            s = f"{cle}={quote(remplacements[cle], safe='')}"
            vus.add(cle)
        segments.append(s)
    for cle, valeur in (ajouter or {}).items():
        if cle not in vus:
            segments.append(f"{cle}={quote(valeur, safe='')}")
    return "&".join(segments)


def retour(rappel_client: str, parametres: list) -> str:
    """Le rappel du client, paramètres de réponse REMPLACÉS, jamais ajoutés : un `iss`
    glissé d'avance dans ce rappel survivrait sinon à côté du nôtre (mix-up)."""
    p = urlsplit(rappel_client)
    garde = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k not in _CLES_DE_REPONSE]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(garde + list(parametres)), ""))


# ── l'échange de jeton ────────────────────────────────────────────────────────

_client: Optional[httpx.AsyncClient] = None


async def _client_http() -> httpx.AsyncClient:
    """Un client PARTAGÉ, construit hors boucle au premier échange : sa construction charge
    le magasin de certificats de façon synchrone (plusieurs secondes à froid)."""
    global _client
    if _client is None:
        _client = await run_in_threadpool(httpx.AsyncClient,
                                          timeout=httpx.Timeout(_JETON_TIMEOUT))
    return _client


async def _poster(url: str, corps: bytes, headers: dict) -> httpx.Response:
    client = await _client_http()
    return await asyncio.wait_for(client.post(url, content=corps, headers=headers),
                                  timeout=_JETON_TIMEOUT)


def ip_observee(request: Request) -> str:
    """L'adresse qu'a VUE le relais le plus proche, jamais une valeur que le client écrit.

    ⚠️ Pas `CF-Connecting-IP` (`client_trace.pick_ip`) : le host de production n'est pas
    derrière Cloudflare, et un en-tête que personne n'écrase se forge à chaque requête —
    un seau clefé dessus ne freine rien. Caddy ignore le `X-Forwarded-For` reçu d'un
    client non approuvé et y pose l'adresse du pair TCP : son DERNIER segment est donc ce
    que le relais a observé (derrière Cloudflare, l'arête — plus grossier, jamais forgé)."""
    xff = request.headers.get("x-forwarded-for", "")
    dernier = xff.split(",")[-1].strip() if xff else ""
    return dernier or (request.client.host if request.client else "") or "unknown"


def _seau_ok(cle: str, maintenant: float) -> bool:
    jetons, dernier = _seaux.get(cle, (_SEAU_RAFALE, maintenant))
    jetons = min(_SEAU_RAFALE, jetons + (maintenant - dernier) * _SEAU_PAR_MIN / 60.0)
    _seaux[cle] = (jetons - 1.0 if jetons >= 1.0 else jetons, maintenant)
    while len(_seaux) > _SEAUX_MAX:
        del _seaux[next(iter(_seaux))]
    return jetons >= 1.0


def _refus(code: str, detail: str, statut: int = 400, cors: bool = False, **headers):
    h = {"cache-control": "no-store", **headers, **(_cors() if cors else {})}
    return JSONResponse({"error": code, "error_description": detail}, status_code=statut,
                        headers=h)


def _cors() -> dict:
    return {"Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "authorization, content-type"}


def make_routes(public_url: str, claude_app_id: str) -> list[Route]:
    """Montées par la façade sur TOUT host : une déclaration retirée arrête d'annoncer et de
    relayer, jamais de servir un client qui a lu la métadonnée du relais."""

    def cible(request: Request) -> Cible:
        return cible_pour_host(facade._host_of(request), public_url, claude_app_id)

    async def authorize(request: Request) -> Response:
        c = cible(request)
        requete = request.scope.get("query_string", b"").decode("latin-1")
        demande, raison = lire_demande(requete)
        if not relais_actif(c.host):
            raison = "not_declared"
        elif demande is not None and (not c.app_id or demande["client_id"] != c.app_id):
            raison = "foreign_client"
        elif demande is not None and not facade._redirect_ok(demande["redirect_uri"]):
            raison = "redirect_not_allowed"
        # Le host du rappel, pour le journal seulement — lu par la garde, qui ne lève
        # jamais : un `redirect_uri` que `urlsplit` refuse doit se replier, pas rendre 500.
        canon = facade._uri_canonique(demande["redirect_uri"]) if demande else None
        rappel_host = canon.hostname if canon is not None else None
        if raison:
            # %r sur tout ce que le client écrit : une valeur décodée peut porter un saut
            # de ligne et forger une ligne `oauth.relay` dans le journal.
            _log.info("oauth.relay authorize mode=direct reason=%s host=%s client=%r "
                      "redirect_host=%r", raison, c.host, demande and demande["client_id"],
                      rappel_host)
            return redirection(c.oidc_public, requete, consentement=c.consentement)
        etat = sceller_etat(secret(), c.as_base, demande["redirect_uri"], demande["state"])
        _log.info("oauth.relay authorize mode=relay host=%s client=%r redirect_host=%r",
                  c.host, c.app_id, rappel_host)
        return redirection(c.oidc_public,
                           reecrire(requete, {"redirect_uri": c.rappel, "state": etat},
                                    ajouter={"state": etat}),
                           consentement=c.consentement)

    async def callback(request: Request) -> Response:
        c = cible(request)
        p = request.query_params
        cle = secret()
        ouvert, raison = ouvrir_etat(cle, p.get("state", ""), c.as_base) if cle \
            else (None, "no_secret")
        if ouvert is None:
            _log.warning("oauth.relay callback outcome=bad_state:%s host=%s", raison, c.host)
            return _refus("invalid_request", "état d'autorisation invalide ou expiré : "
                                             "recommencer la connexion depuis le client")
        rappel_client, etat_client, age = ouvert
        rappel_host = urlsplit(rappel_client).hostname
        iss = p.get("iss")
        if iss is not None and iss.rstrip("/") not in c.emetteurs:
            # Journal seul : un événement de suivi d'erreurs partirait avec l'adresse de
            # la requête, donc avec le code d'autorisation qu'elle porte.
            _log.warning("oauth.relay callback outcome=iss_mismatch host=%s iss=%r "
                         "attendus=%r", c.host, iss, sorted(c.emetteurs))
            return _refus("invalid_request", "réponse d'autorisation d'un émetteur inattendu")
        if p.get("code"):
            parametres, issue = [("code", marquer_code(cle, c.as_base, rappel_client,
                                                       p["code"]))], "code"
        elif p.get("error"):
            parametres = [(k, p[k]) for k in ("error", "error_description", "error_uri")
                          if p.get(k)]
            issue = f"error:{p['error']}"
        else:
            _log.warning("oauth.relay callback outcome=empty host=%s", c.host)
            return _refus("invalid_request", "réponse d'autorisation sans code ni erreur")
        if etat_client is not None:
            parametres.append(("state", etat_client))
        parametres.append(("iss", c.issuer))
        _log.info("oauth.relay callback outcome=%r host=%s redirect_host=%r age_s=%d",
                  issue, c.host, rappel_host, age)
        return Response(status_code=302, headers={
            "location": retour(rappel_client, parametres), "cache-control": "no-store"})

    async def token(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_cors())
        c = cible(request)
        if not _seau_ok(ip_observee(request), time.monotonic()):
            return _refus("temporarily_unavailable", "trop de requêtes — réessayer", 429,
                          cors=True, **{"retry-after": "30"})
        if not request.headers.get("content-type", "").lower().startswith(
                "application/x-www-form-urlencoded"):
            return _refus("invalid_request", "corps attendu en "
                          "application/x-www-form-urlencoded (RFC 6749 §4.1.3)", cors=True)
        brut = await request.body()
        if len(brut) > _CORPS_MAX:
            return _refus("invalid_request", "corps trop volumineux", cors=True)
        corps = brut.decode("utf-8", "replace")
        paires = parse_qsl(corps, keep_blank_values=True)
        cles, v = [k for k, _ in paires], dict(paires)
        if cles.count("grant_type") != 1 or v["grant_type"] not in _GRANTS:
            return _refus("unsupported_grant_type", "seuls authorization_code et "
                          "refresh_token sont servis", cors=True)
        forme = "-"
        if v["grant_type"] == "authorization_code":
            if cles.count("code") != 1 or cles.count("redirect_uri") > 1:
                return _refus("invalid_request", "code et redirect_uri : une fois chacun",
                              cors=True)
            marque = v["code"].startswith(CODE_MARQUE + ".")
            forme = "marked" if marque else "unmarked"
            if marque:
                cle = secret()
                code = lire_code(cle, c.as_base, v.get("redirect_uri"), v["code"]) if cle \
                    else None
                if code is None:
                    _log.warning("oauth.relay token grant=authorization_code code=tag_mismatch "
                                 "host=%s", c.host)
                    return _refus("invalid_grant", "le code ne correspond pas au redirect_uri "
                                  "de la demande d'autorisation", cors=True)
                corps = reecrire(corps, {"code": code, "redirect_uri": c.rappel})
            elif v.get("redirect_uri") == c.rappel:
                # Un client légitime n'envoie JAMAIS le rappel de la façade : c'est un code
                # relayé dont on a retiré la marque pour sauter sa vérification.
                _log.warning("oauth.relay token grant=authorization_code code=mark_stripped "
                             "host=%s", c.host)
                return _refus("invalid_grant", "le code ne correspond pas au redirect_uri "
                              "de la demande d'autorisation", cors=True)
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "Accept": "application/json", "User-Agent": facade._UA}
        if request.headers.get("authorization"):
            headers["Authorization"] = request.headers["authorization"]
        global _en_vol
        if _en_vol >= _EN_VOL_MAX:
            _log.warning("oauth.relay token grant=%s code=%s upstream=saturated host=%s",
                         v["grant_type"], forme, c.host)
            return _refus("temporarily_unavailable", "trop d'échanges en cours — réessayer",
                          503, cors=True, **{"retry-after": "5"})
        debut = time.monotonic()
        _en_vol += 1
        try:
            amont = await _poster(f"{c.oidc_jeton}/token", corps.encode(), headers)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            _log.warning("oauth.relay token grant=%s code=%s upstream=timeout host=%s",
                         v["grant_type"], forme, c.host)
            return _refus("server_error", "le serveur d'autorisation n'a pas répondu", 504,
                          cors=True)
        except httpx.HTTPError as exc:
            _log.warning("oauth.relay token grant=%s code=%s upstream=%s host=%s",
                         v["grant_type"], forme, type(exc).__name__, c.host)
            return _refus("server_error", "serveur d'autorisation injoignable", 502, cors=True)
        finally:
            _en_vol -= 1
        ms = int((time.monotonic() - debut) * 1000)
        json_amont = amont.headers.get("content-type", "").startswith("application/json")
        erreur = ""
        if json_amont and amont.status_code >= 400:
            try:
                erreur = str(amont.json().get("error", ""))[:40]
            # noqa: SILENT — la raison n'est qu'une étiquette de journal, le corps part tel quel
            except Exception:
                erreur = "?"
        _log.info("oauth.relay token grant=%s code=%s upstream=%d error=%s ms=%d host=%s",
                  v["grant_type"], forme, amont.status_code, erreur or "-", ms, c.host)
        if amont.status_code >= 500 or not json_amont:
            return _refus("server_error", "réponse inattendue du serveur d'autorisation", 502,
                          cors=True)
        garder = {k: amont.headers[k] for k in ("content-type", "cache-control", "pragma",
                                                "www-authenticate") if k in amont.headers}
        garder.setdefault("cache-control", "no-store")
        return Response(content=amont.content, status_code=amont.status_code,
                        headers={**garder, **_cors()})

    return [Route(AUTHORIZE_PATH, authorize, methods=["GET"]),
            Route(CALLBACK_PATH, callback, methods=["GET"]),
            Route(TOKEN_PATH, token, methods=["POST", "OPTIONS"])]


# ── maintenance : poser le rappel de la façade sur les hosts déclarés ─────────

def poser_les_rappels(*, dry_run: bool = True) -> dict:
    """`oto-mcp maintenance oauth-relay-callbacks [--apply]` — la mise en service d'un host.

    ⚠️ **Le host doit DÉJÀ figurer dans `OTO_MCP_OAUTH_RELAY_HOSTS`** du `.env` que la
    commande charge — c'est la seule liste qu'elle parcourt —, et le service ne pas encore
    avoir redémarré : il n'annoncera le relais qu'au redémarrage, rappel posé.

    Pose `<as_base>/oauth/callback` sur l'application partagée de l'annuaire de chaque host
    déclaré, puis RELIT l'application pour le constater (Logto remplace la liste entière à
    chaque écriture, sans contrôle de concurrence : une écriture promise ne vaut pas une
    écriture constatée). À blanc par défaut (constate seulement), `--apply` écrit. Chaque
    host est tenté et rendu, même si un autre échoue. Un host dont la plateforme
    n'administre pas l'annuaire est rendu `manuel`, avec le rappel exact à faire poser."""
    from .. import db, server, tenancy
    declares = sorted(hosts_declares())
    if not declares:
        return {"(aucun)": f"{_HOSTS_ENV} est vide dans cet environnement : rien à poser"}
    # Une base illisible ferait passer chaque tenant pour « inconnu » (le registre du boot
    # l'avale) : ici elle doit ÉCHOUER, pas rendre un diagnostic faux.
    db.list_tenant_issuers()
    registre, _ = server._registry_and_issuers()
    tenancy.install(registre)
    public_url = os.environ.get("OTO_MCP_PUBLIC_URL", "")
    app_plateforme = os.environ.get("OTO_MCP_CLAUDE_APP_ID", "")
    rendu = {}
    for host in declares:
        c = cible_pour_host(host, public_url, app_plateforme)
        if c.host != host:
            rendu[host] = "inconnu : ni le domaine de la plateforme, ni celui d'un tenant"
            continue
        if not c.app_id or c.directory is None:
            rendu[host] = f"manuel : poser {c.rappel} sur l'application {c.app_id or '?'}"
            continue
        try:
            if not dry_run:
                facade._register_redirects(c.app_id, [c.rappel], c.directory, cors_uris=[])
            present = c.rappel in facade._redirect_uris(c.app_id, c.directory)
        except Exception as exc:
            _log.warning("oauth.relay maintenance host=%s échec", host, exc_info=True)
            rendu[host] = f"échec : {type(exc).__name__}"
            continue
        if dry_run:
            rendu[host] = "présent" if present else f"absent : {c.rappel} (rejouer avec --apply)"
        else:
            rendu[host] = "posé" if present else "NON CONSTATÉ après écriture"
    return rendu
