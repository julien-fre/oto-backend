"""#472 — `/mcp` et `/api/*` ANNONCENT l'UTF-8 qu'ils écrivent déjà.

Ce lot n'est pas la réparation d'un incident : les octets servis sont de l'UTF-8
valide, et ils l'ont toujours été (`test_les_octets_etaient_deja_bons` le fige). Ce
qui manquait est l'étiquette. Un client qui suit la spec HTTP applique à un
`text/*` sans `charset` le défaut ISO-8859-1 et lit « dÃ©jÃ  » : le serveur avait
raison sur le fil et tort dans l'en-tête, et c'est le client consciencieux qui
payait.

Le test qui compte est donc le DIFFÉRENTIEL sur un vrai socket
(`test_requests_sans_reglage_lit_deja_avec_ses_accents`) : la même app, avec et
sans la couche, lue par `requests` **sans aucun réglage d'encodage** — mêmes octets
des deux côtés, deux lectures opposées.

L'assemblage (la couche est bien SERVIE, sous la garde de déconnexion et au-dessus
du dispatch par Host) est figé par
`tests/test_client_disconnect_guard.py::test_lapp_racine_servie_par_uvicorn_est_gardee`
— la retirer de `build_root_app` y fait rouge.

Logique de transport pure : aucun accès DB.
"""
from __future__ import annotations

import contextlib
import threading
import time

import pytest
import requests
import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp.response_charset import ResponseCharset, completer_content_type

# La description porte les deux motifs cités dans #472 — c'est elle qu'on relit sur
# le fil, pas une chaîne de test neutre.
DESCRIPTION = "Un outil déjà configuré (Identité MCP courante)."

# Ce que LIT un client qui applique le défaut ISO-8859-1 de HTTP/1.1 à un `text/*`
# sans charset. Dérivé, jamais recopié : « dÃ©jÃ  » se termine par U+00A0 (insécable
# — c'est la lecture latin-1 du second octet de « à »), et un littéral collé depuis
# un rapport porte une espace ordinaire, donc ne matche jamais.
MOJIBAKE = DESCRIPTION.encode("utf-8").decode("latin-1")

_ENTETES = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def _app_mcp(json_response: bool = False):
    """Une instance FastMCP minimale — MÊME transport (SDK `mcp`) qu'en prod."""
    srv = FastMCP("charset-472")

    @srv.tool
    def deja(x: str = "") -> str:
        """Un outil déjà configuré (Identité MCP courante)."""
        return x

    return srv.http_app(json_response=json_response)


def _initialize(post, url):
    """Handshake complet ; rend la réponse et les en-têtes de session à réutiliser."""
    r = post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "test-472", "version": "0"}}},
             headers=_ENTETES)
    assert r.status_code == 200, r.text
    suite = dict(_ENTETES)
    sid = r.headers.get("mcp-session-id")
    if sid:
        suite["mcp-session-id"] = sid
    post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
         headers=suite)
    return r, suite


def _tools_list(post, url, entetes):
    r = post(url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
             headers=entetes)
    assert r.status_code == 200, r.text
    return r


# ── Le contrat d'en-tête, sur les deux modes de réponse de /mcp ───────────────

@pytest.mark.parametrize("json_response,type_attendu",
                         [(False, "text/event-stream"), (True, "application/json")])
def test_initialize_puis_tools_list_annoncent_charset_utf8(json_response, type_attendu):
    """Les deux modes que le SDK sait servir : SSE (le nôtre en prod) et JSON."""
    with TestClient(ResponseCharset(_app_mcp(json_response))) as c:
        r_init, entetes = _initialize(c.post, "/mcp/")
        r_list = _tools_list(c.post, "/mcp/", entetes)

    for etape, r in (("initialize", r_init), ("tools/list", r_list)):
        ct = r.headers["content-type"]
        assert ct.split(";")[0].strip() == type_attendu, etape
        assert "charset=utf-8" in ct.lower(), f"{etape} : {ct!r}"


def test_lerreur_de_transport_json_de_mcp_est_etiquetee_aussi():
    """En prod le serveur sert SSE — mais ses erreurs de transport partent en
    `application/json` (`_create_error_response` du SDK). Les deux content-types
    coexistent donc sur `/mcp`, et les deux doivent porter le charset."""
    with TestClient(ResponseCharset(_app_mcp())) as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   headers={**_ENTETES, "Accept": "application/json"})
    assert r.status_code == 406                       # Accept sans text/event-stream
    assert r.headers["content-type"] == "application/json; charset=utf-8"


# ── La preuve d'effet : un vrai socket, `requests`, aucun réglage ─────────────

@contextlib.contextmanager
def _servi(app):
    """Sert `app` sur un port libre — le seul moyen de faire lire `requests`, qui
    devine l'encodage depuis l'en-tête (`TestClient` court-circuite le fil)."""
    serveur = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0,
                                            log_level="warning"))
    fil = threading.Thread(target=serveur.run, daemon=True)
    fil.start()
    try:
        limite = time.time() + 30
        while not serveur.started:
            assert time.time() < limite, "uvicorn n'a pas démarré"
            time.sleep(0.02)
        port = serveur.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}/mcp/"
    finally:
        serveur.should_exit = True
        fil.join(timeout=30)


def _tools_list_par_requests(app):
    """`tools/list` lu par `requests` SANS toucher à `response.encoding`."""
    with _servi(app) as url:
        with requests.Session() as s:
            _, entetes = _initialize(s.post, url)
            return _tools_list(s.post, url, entetes)


def test_requests_sans_reglage_lit_deja_avec_ses_accents():
    """LE test du lot. Même app, deux assemblages, deux lectures opposées."""
    nu = _tools_list_par_requests(_app_mcp())                   # avant : SSE nu
    garde = _tools_list_par_requests(ResponseCharset(_app_mcp()))

    # Sans la couche : le client suit la spec, devine ISO-8859-1, et voit du mojibake.
    assert nu.encoding == "ISO-8859-1"
    assert MOJIBAKE in nu.text
    assert DESCRIPTION not in nu.text

    # Avec : `requests` lit ce que le serveur a écrit, sans qu'on lui souffle rien.
    assert garde.encoding == "utf-8"
    assert DESCRIPTION in garde.text
    assert MOJIBAKE not in garde.text


def test_les_octets_etaient_deja_bons():
    """Le corollaire, et la raison pour laquelle ce lot est une PROTECTION et pas un
    correctif de contenu : des deux côtés, le fil porte le même UTF-8 valide. Seule
    l'étiquette change — donc aucune description d'outil n'est modifiée."""
    attendu = DESCRIPTION.encode("utf-8")
    for app in (_app_mcp(), ResponseCharset(_app_mcp())):
        assert attendu in _tools_list_par_requests(app).content


# ── La face REST `/api/*` : elle non plus ne portait pas de charset ───────────

def test_une_reponse_json_rest_porte_le_charset():
    """`api.base._json` construit une `JSONResponse` : `media_type` ne commence pas
    par `text/`, donc Starlette n'y met AUCUN charset de lui-même. Vérifié ici sur le
    helper réel, celui que tous les handlers REST empruntent."""
    from oto_mcp.api.base import _json

    async def handler(request):
        return _json(request, {"ok": True, "note": "déjà"})

    app = ResponseCharset(Starlette(routes=[Route("/api/ping", handler)]))
    with TestClient(app) as c:
        r = c.get("/api/ping")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json; charset=utf-8"


# ── Ce que la couche ne touche PAS ────────────────────────────────────────────

@pytest.mark.parametrize("valeur,attendu", [
    # complétés
    (b"text/event-stream", b"text/event-stream; charset=utf-8"),
    (b"application/json", b"application/json; charset=utf-8"),
    (b"Application/JSON", b"Application/JSON; charset=utf-8"),   # casse préservée
    # déjà étiquetés → intacts à l'octet près
    (b"text/event-stream; charset=utf-8", b"text/event-stream; charset=utf-8"),
    (b"application/json;charset=UTF-8", b"application/json;charset=UTF-8"),
    (b"text/markdown; charset=utf-8", b"text/markdown; charset=utf-8"),
    # hors allowlist → intacts (le serveur sert aussi du binaire)
    (b"application/pdf", b"application/pdf"),
    (b"application/zip", b"application/zip"),
    (b"image/svg+xml", b"image/svg+xml"),
    # pas de règle `text/*` générique : on complète ce qu'on a mesuré, rien de plus
    (b"text/html", b"text/html"),
    (b"text/plain", b"text/plain"),
])
def test_completion_par_type(valeur, attendu):
    assert completer_content_type(valeur) == attendu


def test_une_reponse_sans_content_type_traverse_intacte():
    """Un 204 (préflight CORS de `api.base`) n'a rien à étiqueter."""
    vus = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        vus.append(message)

    async def receive():                                # pragma: no cover
        raise AssertionError("le corps n'est jamais lu")

    import anyio
    anyio.run(ResponseCharset(app), {"type": "http", "path": "/x"}, receive, send)
    assert vus[0]["headers"] == []


def test_hors_http_la_couche_ne_wrappe_rien():
    """lifespan et websocket : pas d'en-tête de réponse, donc pas de wrapper à payer
    — et le `send` d'origine doit arriver INTACT à l'app (le lifespan de FastMCP
    démarre le session-manager par ce canal)."""
    recus = []

    async def app(scope, receive, send):
        recus.append((scope["type"], send))

    async def send(message):                            # pragma: no cover
        raise AssertionError("jamais appelé")

    async def receive():                                # pragma: no cover
        raise AssertionError("jamais appelé")

    import anyio
    for t in ("lifespan", "websocket"):
        anyio.run(ResponseCharset(app), {"type": t}, receive, send)
    assert [t for t, _ in recus] == ["lifespan", "websocket"]
    assert all(s is send for _, s in recus)
