"""Le 401 de `/mcp` dit ce qu'est oto et comment s'y connecter (oto-backend#1071).

Servi par le VRAI serveur : `server._build_mcp` avec l'authentification, `http_app()`,
les routes de la façade et les middlewares ASGI de `main`, dans leur ordre. Deux apps
construites côte à côte — avec et sans `McpAccueilMiddleware` — pour prouver que la
négociation OAuth (statut, `WWW-Authenticate`, découverte) ne bouge pas d'un octet.
"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from fastmcp.server.auth import AccessToken, TokenVerifier

from oto_mcp.mcp_accueil import LLMS_TXT, McpAccueilMiddleware

_BON = "jeton-valide"

_INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "banc", "version": "0"}},
}
_ENTETES_MCP = {"accept": "application/json, text/event-stream",
                "content-type": "application/json"}


class _Verificateur(TokenVerifier):
    """Accepte un seul jeton : le reste de la chaîne d'auth est celle de fastmcp."""

    async def verify_token(self, token: str) -> "AccessToken | None":
        if token != _BON:
            return None
        return AccessToken(token=token, client_id="banc", scopes=[],
                           expires_at=None, claims={"sub": "banc-1071"})


def _app(accueil: bool):
    """L'app authentifiée telle que `main` l'assemble (hors REST, sans objet ici)."""
    from oto_mcp import server
    from oto_mcp.auth import facade as oauth_facade
    mcp = server._build_mcp("streamable_http", _Verificateur())
    app = mcp.http_app()
    for route in reversed(oauth_facade.make_routes("https://mcp.oto.cx", "app-banc")):
        app.router.routes.insert(0, route)
    if accueil:
        app.add_middleware(McpAccueilMiddleware)
    app.add_middleware(server.TenantChallengeMiddleware)
    return app


@pytest.fixture(scope="module")
def apps():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.oto.cx")
        mp.setenv("LOGTO_ENDPOINT", "https://auth.oto.cx")
        mp.setenv("OTO_MCP_CLAUDE_APP_ID", "app-banc")
        yield _app(accueil=True), _app(accueil=False)


def _post_init(app, host="mcp.oto.cx", jeton=None):
    entetes = {**_ENTETES_MCP, "host": host}
    if jeton:
        entetes["authorization"] = f"Bearer {jeton}"
    return TestClient(app).post("/mcp", json=_INITIALIZE, headers=entetes)


# --- sans jeton : le texte, et la négociation intacte ----------------------------

def test_initialize_sans_jeton_rend_un_401_qui_dit_quoi_faire(apps):
    servie, avant = apps
    r, ref = _post_init(servie), _post_init(avant)
    assert r.status_code == ref.status_code == 401
    assert ref.content == b"", "l'état d'avant : un 401 au corps vide"
    assert LLMS_TXT in r.text, r.text
    assert "https://mcp.oto.cx/mcp" in r.text
    assert "oto-plugin" in r.text and "oto-cli" in r.text
    assert len(r.text.strip().splitlines()) <= 10
    assert r.headers["content-type"].startswith("text/plain")
    assert int(r.headers["content-length"]) == len(r.content)


def test_www_authenticate_est_inchange_au_octet_pres(apps):
    servie, avant = apps
    r, ref = _post_init(servie), _post_init(avant)
    assert r.headers["www-authenticate"] == ref.headers["www-authenticate"]
    assert 'resource_metadata="https://mcp.oto.cx/.well-known/oauth-protected-resource' \
        in r.headers["www-authenticate"]


def test_un_get_de_navigateur_recoit_le_meme_texte(apps):
    servie, avant = apps
    r = TestClient(servie).get("/mcp", headers={"host": "mcp.oto.cx",
                                                 "accept": "text/html"})
    ref = TestClient(avant).get("/mcp", headers={"host": "mcp.oto.cx",
                                                 "accept": "text/html"})
    assert r.status_code == ref.status_code == 401
    assert r.headers["www-authenticate"] == ref.headers["www-authenticate"]
    assert LLMS_TXT in r.text
    assert r.text == _post_init(servie).text


def test_un_jeton_invalide_garde_son_json_d_erreur_augmente(apps):
    servie, avant = apps
    r, ref = _post_init(servie, jeton="faux"), _post_init(avant, jeton="faux")
    assert r.status_code == ref.status_code == 401
    assert r.headers["www-authenticate"] == ref.headers["www-authenticate"]
    assert r.headers["content-type"] == ref.headers["content-type"]
    doc, doc_ref = r.json(), ref.json()
    assert {k: v for k, v in doc.items() if k != "help"} == doc_ref
    assert LLMS_TXT in doc["help"]


# --- avec un jeton valide : rien ne change --------------------------------------

def test_avec_un_jeton_valide_rien_ne_change(apps):
    """Le jeton passe l'auth ; la réponse est celle du TRANSPORT, octet pour octet.
    Un `GET /mcp` sans session : le transport répond sans rien demander à la base, ce
    qui garde ce banc jouable sans PostgreSQL, comme la CI. Une app neuve par client :
    le gestionnaire de sessions ne démarre qu'une fois par instance."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.oto.cx")
        mp.setenv("LOGTO_ENDPOINT", "https://auth.oto.cx")
        mp.setenv("OTO_MCP_CLAUDE_APP_ID", "app-banc")
        reponses = []
        for accueil in (True, False):
            with TestClient(_app(accueil)) as c:
                reponses.append(c.get("/mcp", headers={
                    "host": "mcp.oto.cx", "accept": "text/event-stream",
                    "authorization": f"Bearer {_BON}"}))
    r, ref = reponses
    assert r.status_code == ref.status_code
    assert r.status_code not in (401, 403), "le jeton valide doit passer l'auth"
    assert r.content == ref.content
    assert r.headers.get("content-type") == ref.headers.get("content-type")
    assert LLMS_TXT not in r.text


# --- la découverte OAuth ne bouge pas --------------------------------------------

@pytest.mark.parametrize("chemin", [
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
])
def test_les_metadonnees_de_decouverte_sont_inchangees(apps, chemin):
    servie, avant = apps
    r = TestClient(servie).get(chemin, headers={"host": "mcp.oto.cx"})
    ref = TestClient(avant).get(chemin, headers={"host": "mcp.oto.cx"})
    assert r.status_code == ref.status_code
    assert r.content == ref.content
    assert r.headers.get("content-type") == ref.headers.get("content-type")


# --- ailleurs que sur l'hôte principal d'oto : le 401 d'avant --------------------

@pytest.mark.parametrize("host", [
    "mcp.partenaire.test",        # une instance servie ailleurs (ADR 0070)
    "mcp.acme.test",              # l'hôte d'un tenant (ADR 0052)
    "acme--mcp.oto.ninja",        # l'endpoint d'une org
    "projet.mcp.oto.cx",          # un projet publié en portée org
])
def test_hors_de_l_hote_principal_le_401_est_celui_d_avant(apps, host):
    servie, avant = apps
    r, ref = _post_init(servie, host=host), _post_init(avant, host=host)
    assert r.status_code == ref.status_code == 401
    assert r.content == ref.content == b""
    assert r.headers["www-authenticate"] == ref.headers["www-authenticate"]


def test_la_preproduction_nomme_son_propre_endpoint(apps):
    servie, _ = apps
    r = _post_init(servie, host="mcp.oto.ninja")
    assert "https://mcp.oto.ninja/mcp" in r.text and LLMS_TXT in r.text


def test_un_401_hors_de_mcp_n_est_pas_touche():
    """La face REST a ses propres 401 (JSON) : ils ne sont pas les nôtres."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    corps = {"error": "unauthorized"}
    app = Starlette(routes=[Route("/api/me", lambda r: JSONResponse(corps, 401))])
    app.add_middleware(McpAccueilMiddleware)
    r = TestClient(app).get("/api/me", headers={"host": "mcp.oto.cx"})
    assert r.status_code == 401 and json.loads(r.content) == corps
