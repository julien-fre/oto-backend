"""Ce que sert oto SANS authentification — figé, sur le vrai chemin HTTP (oto-backend#534).

Le registre MCP se construit UNE fois. Le mode non authentifié (un projet publié en
`anonymous` ou `secret`, ADR 0032) n'est pas un second serveur : c'est une FACE du même
registre, montée sans auth, dont la visibilité est restreinte par
`anon_visibility.AnonymousVisibilityMiddleware` à l'allowlist calculée par
`subdomain_project.current_allowlist` — la règle n'est écrite qu'à ces deux endroits.

Ce banc ne simule rien du chemin : `HostDispatch` → face anonyme → session MCP réelle
(`initialize`, `tools/list`, `tools/call`), contre le catalogue construit par
`_build_mcp`. Seule la lecture du projet publié est remplacée (pas de base, comme la CI).

Les listes attendues sont LITTÉRALES : elles ont été relevées sur l'assemblage d'avant
(deux `_build_mcp`, l'instance anonyme portant seule le middleware de visibilité) et
n'ont pas bougé d'un nom avec l'assemblage à registre unique. Un nom qui apparaît ou
disparaît ici change ce qu'un inconnu voit d'oto : ce n'est jamais un détail.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from oto_mcp import subdomain_project as sp
from oto_mcp.anon_visibility import face_anonyme

from _mcp_app import static_mcp

_DOMAINE = "oto.test"
_ENTETES = {"accept": "application/json, text/event-stream",
            "content-type": "application/json"}
_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "banc-534", "version": "0"}}}

# L'allowlist d'un preset mêle exprès : des outils du catalogue, deux outils JAMAIS
# servis sans `sub` (`NEVER_ANON_TOOLS`), un outil de pages et un nom inconnu.
_PRESET = ["fr_search", "serper_search", "oto_search", "oto_doc_app", "oto_doc",
           "outil_inconnu_534"]

# (posture et opt-ins du projet publié) -> ce que liste la face anonyme, ni plus ni moins.
SCENARIOS = {
    "anonymous": (
        {"mcp_access": "anonymous"},
        ["fr_search", "serper_search"]),
    "anonymous_opt_ins_ignores": (
        # les opt-ins ne valent qu'en `secret` : un endpoint public ne les honore pas
        {"mcp_access": "anonymous", "mcp_expose_datastore": True,
         "mcp_expose_datastore_write": True, "mcp_expose_docs": True},
        ["fr_search", "serper_search"]),
    "secret": (
        {"mcp_access": "secret"},
        ["fr_search", "serper_search"]),
    "secret_datastore_lecture_et_pages": (
        {"mcp_access": "secret", "mcp_expose_datastore": True, "mcp_expose_docs": True},
        ["data_list_datastores", "data_rows", "fr_search", "oto_doc", "serper_search"]),
    "secret_datastore_ecriture": (
        {"mcp_access": "secret", "mcp_expose_datastore": True,
         "mcp_expose_datastore_write": True},
        ["data_list_datastores", "data_rows", "data_set_schema", "data_write",
         "fr_search", "serper_search"]),
}


def _projet(posture: dict) -> dict:
    return {"id": 534, "owner_type": "org", "owner_id": 7, "mcp_slug": "banc",
            "mcp_tools": list(_PRESET), **posture}


def _faces():
    """Les deux faces telles que `main` les assemble, sur UN registre."""
    mcp = static_mcp()
    return mcp, mcp.http_app(), face_anonyme(mcp)


def _jsonrpc(r) -> dict:
    """La réponse JSON-RPC d'un POST /mcp, qu'elle parte en JSON ou en SSE."""
    assert r.status_code == 200, (r.status_code, r.text)
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    for ligne in r.text.splitlines():
        if ligne.startswith("data:"):
            return json.loads(ligne[5:])
    raise AssertionError(f"pas de message JSON-RPC : {r.text!r}")


class _Session:
    def __init__(self, client: TestClient, host: str):
        self.c, self.host, self.sid, self._id = client, host, None, 1
        r = self._post(_INIT)
        self.sid = r.headers.get("mcp-session-id")
        _jsonrpc(r)
        self.c.post("/mcp", headers=self._entetes(),
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _entetes(self) -> dict:
        e = {**_ENTETES, "host": self.host}
        if self.sid:
            e["mcp-session-id"] = self.sid
        return e

    def _post(self, msg: dict):
        return self.c.post("/mcp", headers=self._entetes(), json=msg)

    def requete(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        return _jsonrpc(self._post({"jsonrpc": "2.0", "id": self._id, "method": method,
                                    "params": params or {}}))

    def outils(self) -> list[str]:
        return sorted(t["name"] for t in self.requete("tools/list")["result"]["tools"])


@pytest.fixture
def racine(monkeypatch):
    """L'app racine servie par uvicorn (dispatch par Host), avec un projet publié
    remplaçable : `racine(posture)` rend un client dont le lifespan tourne."""
    monkeypatch.setenv("OTO_PROJECT_DOMAIN", _DOMAINE)
    publie: dict = {}

    async def _resoudre(host):
        return publie.get("p") if sp._slug_from_host(host) == "banc" else None

    monkeypatch.setattr(sp, "resolve_project_async", _resoudre)
    _, app, anon_app = _faces()
    with TestClient(sp.HostDispatch(app, anon_app)) as client:
        def _servir(posture: dict | None) -> TestClient:
            publie["p"] = _projet(posture) if posture is not None else None
            return client
        yield _servir


@pytest.mark.parametrize("nom", sorted(SCENARIOS))
def test_la_face_anonyme_liste_exactement_ce_qu_elle_listait(racine, nom):
    posture, attendu = SCENARIOS[nom]
    s = _Session(racine(posture), f"banc.mcp.{_DOMAINE}")
    assert s.outils() == attendu


def test_la_face_anonyme_n_appelle_pas_un_outil_hors_allowlist(racine):
    """Masqué au listage ET à l'appel : `oto_project` n'est pas dans le preset."""
    s = _Session(racine({"mcp_access": "anonymous"}), f"banc.mcp.{_DOMAINE}")
    rep = s.requete("tools/call", {"name": "oto_project", "arguments": {}})
    assert rep["result"] == {"content": [{"type": "text",
                                          "text": "Unknown tool `oto_project`."}],
                             "isError": True}, rep


def test_la_face_anonyme_atteinte_sans_projet_ne_liste_rien(monkeypatch):
    """Fail-CLOSED : la face anonyme jointe sans contexte de projet résolu (aucun
    chemin de `HostDispatch` n'y mène, c'est la garde du jour où l'un y mènerait)
    ne sert aucun outil — même à froid, avant tout listage réussi."""
    monkeypatch.setattr("oto_mcp.anon_visibility._ALL_NAMES_CACHE", set())
    _, _, anon_app = _faces()
    with TestClient(anon_app) as client:
        assert _Session(client, f"banc.mcp.{_DOMAINE}").outils() == []


def test_la_face_authentifiee_du_meme_registre_n_est_pas_restreinte(racine):
    """Le middleware de visibilité anonyme est dans la chaîne commune : hors de la face
    anonyme il ne masque RIEN. Le host canonique, sur le même registre, liste ce que
    liste un client sans aucune face (en mémoire) — le catalogue entier."""
    from fastmcp import Client

    async def _en_memoire():
        async with Client(static_mcp()) as c:
            return sorted(t.name for t in await c.list_tools())

    attendu = asyncio.run(_en_memoire())
    s = _Session(racine(None), "mcp.oto.test")
    servis = s.outils()
    assert servis == attendu
    assert {"oto_project", "serper_search", "oto_search"} <= set(servis)


def test_un_seul_registre_pour_les_deux_faces():
    """Les deux faces servent LE MÊME serveur : aucun second `register_all`."""
    mcp, app, anon_app = _faces()
    assert app.state.fastmcp_server is mcp
    assert anon_app.state.fastmcp_server is mcp
