"""Le parcours d'autorisation complet, joué par un VRAI client MCP (conventions : « une chaîne
de découverte d'auth se prouve avec un vrai client MCP »).

Le client est `mcp.client.auth.OAuthClientProvider`, celui du SDK installé : découverte
(401 → PRM → métadonnée), enregistrement dynamique, autorisation, retour, échange de jeton,
puis l'appel authentifié. L'annuaire est un Logto FACTICE mais STRICT comme oidc-provider :
rappel enregistré à l'identique, PKCE S256, `redirect_uri` de l'échange égal à celui de la
demande, code à usage unique, et `iss` = son émetteur estampillé sur chaque réponse.

⚠️ **La règle RFC 9207 est appliquée ici même si le SDK installé ne l'applique pas.** Le SDK
épinglé par ce dépôt (1.x) ne compare pas `iss` ; le 2.0 le fait
(`validate_authorization_response_iss`) et c'est lui qui a échoué en production. Le banc
compare donc `iss` à l'`issuer` découvert, comme le 2.0, et passe `iss` au SDK quand celui-ci
sait le recevoir. Le témoin (`test_sans_declaration…`) prouve que ce banc voit le défaut :
c'est la production d'avant, à l'octet près.

Deux annuaires : celui d'un TENANT (le cas vécu) et le NÔTRE (consentement oto#202 posé,
échange de jeton parti vers l'origine).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import sys
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx
import pytest
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from oto_mcp import tenancy
from oto_mcp.auth import facade, relay

try:                                    # SDK 2.0 : le retour porte `iss`
    from mcp.client.auth import AuthorizationCodeResult
except ImportError:                     # SDK 1.x : `(code, state)`
    AuthorizationCodeResult = None
from mcp.client.auth import OAuthClientProvider, OAuthFlowError
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

# Le client HTTP du SDK : `httpx` en 1.x, sa bifurcation `httpx2` en 2.0. On prend celui sur
# lequel `OAuthClientProvider` est bâti, sinon son `auth` est refusé par l'autre.
_HTTP_CLIENT = sys.modules[next(c.__module__ for c in OAuthClientProvider.__mro__
                                if c.__name__ == "Auth").split(".")[0]]

_RAPPEL_CLIENT = "http://127.0.0.1:27890/callback"


@dataclass(frozen=True)
class _Scenario:
    mcp: str          # le host MCP (et de la façade)
    annuaire: str     # le host de Logto
    app: str          # l'application partagée que la DCR rend


_TENANT = _Scenario("mcp.acme.test", "auth.acme.test", "app-acme")
_PLATEFORME = _Scenario("mcp.oto.test", "auth.oto.test", "app-oto")


class _LogtoStrict:
    """Un annuaire, réduit à ce qu'oidc-provider VÉRIFIE."""

    def __init__(self, scenario: _Scenario):
        self.app_id, self.emetteur = scenario.app, f"https://{scenario.annuaire}/oidc"
        self.rappels = set()          # redirectUris de l'application partagée
        self.codes = {}
        self.jetons = set()
        self.rappels_vus = []         # les redirect_uri reçus à /auth : qui voit Logto ?

    def poser(self, app_id, uris, directory=None, *, cors_uris=None):   # la Management API
        assert app_id == self.app_id
        self.rappels.update(uris)

    async def auth(self, request: Request) -> Response:
        p = request.query_params
        rappel = p.get("redirect_uri", "")
        self.rappels_vus.append(rappel)
        if p.get("client_id") != self.app_id:
            return JSONResponse({"code": "oidc.invalid_client", "iss": self.emetteur}, 400)
        if rappel not in self.rappels:
            return JSONResponse({"code": "oidc.invalid_redirect_uri", "iss": self.emetteur}, 400)
        if p.get("code_challenge_method") != "S256" or not p.get("code_challenge"):
            return JSONResponse({"code": "oidc.invalid_request", "iss": self.emetteur}, 400)
        code = secrets.token_urlsafe(32)
        self.codes[code] = (rappel, p["code_challenge"], p.get("resource"))
        retour = {"code": code, "iss": self.emetteur}
        if p.get("state") is not None:
            retour["state"] = p["state"]
        return Response(status_code=303, headers={"location": f"{rappel}?{urlencode(retour)}"})

    async def token(self, request: Request) -> Response:
        form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
        if form.get("grant_type") == "authorization_code":
            enregistre = self.codes.pop(form.get("code", ""), None)   # usage unique
            if enregistre is None:
                return JSONResponse({"error": "invalid_grant"}, 400)
            rappel, defi, _ = enregistre
            if form.get("redirect_uri") != rappel:
                return JSONResponse({"error": "invalid_grant",
                                     "error_description": "redirect_uri mismatch"}, 400)
            verif = base64.urlsafe_b64encode(hashlib.sha256(
                form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if verif != defi:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, 400)
        elif form.get("grant_type") != "refresh_token" or form.get("refresh_token") not in self.jetons:
            return JSONResponse({"error": "invalid_grant"}, 400)
        acces, rafraichissement = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.jetons.update({acces, rafraichissement})
        return JSONResponse({"access_token": acces, "token_type": "Bearer", "expires_in": 3600,
                             "refresh_token": rafraichissement})

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/oidc/auth", self.auth),
                                 Route("/oidc/token", self.token, methods=["POST"])])


def _serveur_mcp(logto: _LogtoStrict, scenario: _Scenario) -> Starlette:
    """La façade montée par `make_routes`, plus un `/mcp` qui exige un jeton de l'annuaire."""
    async def mcp_route(request: Request) -> Response:
        jeton = request.headers.get("authorization", "").removeprefix("Bearer ")
        if jeton in logto.jetons:
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "invalid_token"}, 401, headers={
            "www-authenticate": f'Bearer resource_metadata="https://{scenario.mcp}'
                                '/.well-known/oauth-protected-resource/mcp"'})
    return Starlette(routes=[*facade.make_routes("https://mcp.oto.test", "app-oto"),
                             Route("/mcp", mcp_route, methods=["GET", "POST"])])


class _Aiguillage:
    """Un seul transport ASGI pour deux hosts : la façade et l'annuaire."""

    def __init__(self, par_host):
        self.par_host = par_host

    async def __call__(self, scope, receive, send):
        host = dict(scope["headers"]).get(b"host", b"").decode().split(":")[0]
        await self.par_host[host](scope, receive, send)


class _Stockage:
    def __init__(self):
        self.jetons, self.client = None, None

    async def get_tokens(self):
        return self.jetons

    async def set_tokens(self, tokens: OAuthToken):
        self.jetons = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info: OAuthClientInformationFull):
        self.client = info


@pytest.fixture(params=[_TENANT, _PLATEFORME], ids=["tenant", "plateforme"])
def banc(request, monkeypatch):
    scenario = request.param
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.test")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-banc")
    monkeypatch.setenv("LOGTO_ACME_MGMT_ID", "cid")
    monkeypatch.setenv("LOGTO_ACME_MGMT_SECRET", "csec")
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_ID", "cid")          # annuaire administré
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_SECRET", "csec")
    monkeypatch.setenv("OTO_MCP_OAUTH_RELAY_HOSTS", scenario.mcp)
    logto = _LogtoStrict(scenario)
    aiguillage = _Aiguillage({scenario.mcp: _serveur_mcp(logto, scenario),
                              scenario.annuaire: logto.app()})
    monkeypatch.setattr(facade, "_register_redirects", logto.poser)
    # le relais compare le rappel du client à ceux que l'application porte VRAIMENT
    monkeypatch.setattr(facade, "_redirect_uris", lambda app_id, d=None: sorted(logto.rappels))

    # l'échange de jeton sort par le VRAI `relay._poster` : seul le transport est branché
    async def _client_http():
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=aiguillage))
    monkeypatch.setattr(relay, "_client_http", _client_http)
    relay._seaux.clear()
    relay._rappels.clear()
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.test/oidc",
        tenants=[{"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc",
                  "hosts": [_TENANT.mcp], "oauth_client_id": _TENANT.app,
                  "logto_mgmt": {"token_endpoint": "https://admin.acme.test",
                                 "api_endpoint": "https://auth.acme.test",
                                 "credential": "LOGTO_ACME_MGMT"}}])))
    yield scenario, logto, aiguillage
    tenancy.install(avant)


async def _connexion(scenario: _Scenario, aiguillage) -> tuple[int, dict]:
    """Ce que fait un client MCP à boucle locale : son navigateur suit les redirections
    jusqu'à son rappel, qu'il lit."""
    transport = _HTTP_CLIENT.ASGITransport(app=aiguillage)
    lu = {}

    async def navigateur(url: str) -> None:
        async with _HTTP_CLIENT.AsyncClient(transport=transport) as nav:
            while not url.startswith(_RAPPEL_CLIENT):
                r = await nav.get(url)
                assert r.status_code in (302, 303), f"{url} → {r.status_code} {r.text}"
                url = r.headers["location"]
        lu.update(parse_qsl(urlparse(url).query))

    async def rappel():
        if AuthorizationCodeResult is not None:     # le SDK applique RFC 9207 lui-même
            return AuthorizationCodeResult(code=lu.get("code"), state=lu.get("state"),
                                           iss=lu.get("iss"))
        async with _HTTP_CLIENT.AsyncClient(transport=transport) as c:
            issuer = (await c.get(f"https://{scenario.mcp}/.well-known/oauth-authorization-server")
                      ).json()["issuer"]
        # RFC 9207 §2.4, à l'identique de `validate_authorization_response_iss` (SDK 2.0)
        if lu.get("iss") is not None and lu["iss"] != issuer:
            raise OAuthFlowError(f"Authorization response iss mismatch: {lu['iss']} != {issuer}")
        return lu.get("code"), lu.get("state")

    fournisseur = OAuthClientProvider(
        server_url=f"https://{scenario.mcp}/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="banc", redirect_uris=[AnyUrl(_RAPPEL_CLIENT)],
            grant_types=["authorization_code", "refresh_token"], response_types=["code"],
            token_endpoint_auth_method="none"),
        storage=_Stockage(), redirect_handler=navigateur, callback_handler=rappel)
    async with _HTTP_CLIENT.AsyncClient(transport=transport, auth=fournisseur) as client:
        r = await client.post(f"https://{scenario.mcp}/mcp",
                              json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    return r.status_code, lu


def test_un_client_mcp_strict_se_connecte_par_le_relais(banc):
    scenario, logto, aiguillage = banc
    statut, retour = asyncio.run(_connexion(scenario, aiguillage))
    assert statut == 200
    assert retour["iss"] == f"https://{scenario.mcp}/"
    # Logto n'a vu QUE le rappel de la façade : celui du client ne lui est plus nécessaire
    assert logto.rappels_vus == [f"https://{scenario.mcp}/oauth/callback"]


def test_sans_declaration_le_meme_client_echoue_sur_liss(banc, monkeypatch):
    """Témoin : un host non déclaré sert la métadonnée et le trajet d'avant — exactement
    le défaut de production, qui s'arrête sur `iss` avant tout jeton."""
    scenario, logto, aiguillage = banc
    monkeypatch.setenv("OTO_MCP_OAUTH_RELAY_HOSTS", "")
    with pytest.raises(OAuthFlowError, match="iss mismatch"):
        asyncio.run(_connexion(scenario, aiguillage))
    assert logto.jetons == set()
