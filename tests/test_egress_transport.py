"""Le transport épinglé : la garde d'egress tient À LA CONNEXION.

`egress.check_url` contrôle une URL puis rend la main ; sans transport épinglé,
le client HTTP résolvait une seconde fois avant de se connecter. Ce banc tient ce
que `oto_mcp/egress_transport.py` ferme :

1. **Le rebinding** — une résolution « publique » au contrôle puis « interne » à
   la connexion est refusée, et AUCUNE socket ne s'ouvre.
2. **Les redirections** — chaque saut ouvre sa propre connexion, donc passe par
   la même décision : un 3xx vers la boucle locale est refusé.
3. **L'identité TLS** — on épingle l'adresse, jamais le nom : SNI et vérification
   du certificat portent le nom d'origine, l'en-tête `Host` aussi.
4. **Les politiques** — une exception nommée (`OTO_EGRESS_ALLOW`) vaut pour la
   destination d'un credential, JAMAIS pour une URL choisie par un agent.
5. **Le câblage** — les lecteurs d'URL et les connecteurs à hôte libre montent
   réellement ce transport : un banc qui n'éprouve que le transport reste vert
   quand personne ne le monte.

Aucun accès réseau : la résolution passe par la couture `egress.resolved_addresses`,
l'ouverture de socket par `egress_transport._ouvrir`, et les seuls serveurs joints
écoutent sur la boucle locale de la machine qui joue le banc.
"""
from __future__ import annotations

import http.server
import importlib
import socket
import ssl
import threading

import pytest
import requests
import urllib3.connection
from urllib3.util import connection as urllib3_connexion

from oto_mcp import egress, egress_transport

PUBLIQUE = "93.184.216.34"
PUBLIQUE_V6 = "2001:4860:4860::8888"


# ── outillage ────────────────────────────────────────────────────────────────

class _Resolveur:
    """Une résolution qui CHANGE d'avis : les `n` premiers appels rendent `avant`,
    les suivants `apres`. Compte ses appels."""
    def __init__(self, avant, apres=None, n: int = 1):
        self.avant, self.apres, self.n, self.appels = set(avant), set(apres or avant), n, 0

    def __call__(self, hote, port):  # noqa: ARG002
        self.appels += 1
        return self.avant if self.appels <= self.n else self.apres


def _table(**table):
    def faux(hote, port):  # noqa: ARG001
        return set(table[hote])
    return faux


@pytest.fixture
def aucune_socket(monkeypatch):
    """Toute ouverture de socket par urllib3 fait échouer le banc — y compris un
    chemin qui ne passerait PAS par le transport épinglé."""
    def interdit(*a, **k):
        pytest.fail(f"une socket a été ouverte : {a!r}")
    monkeypatch.setattr(urllib3_connexion, "create_connection", interdit)


class _Gestionnaire(http.server.BaseHTTPRequestHandler):
    redirection: str | None = None

    def do_GET(self):  # noqa: N802
        self.server.hotes.append(self.headers.get("Host"))
        if self.server.redirection:
            self.send_response(302)
            self.send_header("Location", self.server.redirection)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        corps = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *a):
        pass


@pytest.fixture
def serveur_local():
    """Un serveur HTTP sur la boucle locale ; rend (port, serveur). `serveur.hotes`
    garde l'en-tête `Host` de chaque requête reçue."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gestionnaire)
    srv.hotes, srv.redirection = [], None
    fil = threading.Thread(target=srv.serve_forever, daemon=True)
    fil.start()
    yield srv.server_address[1], srv
    srv.shutdown()
    srv.server_close()


def _agent():
    return egress_transport.session(connector="web_read", field="url",
                                    exceptions_declarees=False)


def _connecteur():
    return egress_transport.session(connector="api", field="base_url",
                                    exceptions_declarees=True)


# ── 1. rebinding ─────────────────────────────────────────────────────────────

def test_une_resolution_qui_change_apres_le_controle_est_refusee(monkeypatch, aucune_socket):
    """Le cas fondateur : `check_url` voit une adresse publique, la connexion verrait
    la boucle locale. Le refus tombe AVANT toute socket."""
    resolveur = _Resolveur([PUBLIQUE], ["127.0.0.1"])
    monkeypatch.setattr(egress, "resolved_addresses", resolveur)
    url = "https://rebond.exemple.test/"
    egress.check_url(url, connector="web_read", field="url", exceptions_declarees=False)
    with pytest.raises(egress.EgressRefused, match="boucle locale"):
        _agent().get(url, timeout=5)
    assert resolveur.appels == 2, "le transport résout lui-même, une fois"


def test_un_hote_qui_resout_public_ET_interne_a_la_connexion_est_refuse(monkeypatch,
                                                                       aucune_socket):
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE, "10.0.0.7"]))
    with pytest.raises(egress.EgressRefused, match="réseau privé"):
        _connecteur().get("https://mixte.exemple.test/", timeout=5)


def test_l_instance_elle_meme_est_refusee_a_la_connexion(monkeypatch, aucune_socket):
    monkeypatch.setattr(egress, "resolved_addresses", _Resolveur([PUBLIQUE]))
    with pytest.raises(egress.EgressRefused, match="oto lui-même"):
        _connecteur().get("https://mcp.exemple.test/api/hooks/h", timeout=5)


def test_un_hote_qui_ne_resout_pas_est_une_erreur_reseau(monkeypatch, aucune_socket):
    """Pas un refus de garde : la même erreur que sans transport épinglé, pour que
    les lecteurs qui distinguent « réseau » de « refusé » continuent de le faire."""
    def introuvable(hote, port):
        raise socket.gaierror(socket.EAI_NONAME, "inconnu")
    monkeypatch.setattr(egress, "resolved_addresses", introuvable)
    with pytest.raises(requests.ConnectionError):
        _agent().get("https://nulle-part.exemple.test/", timeout=5)


# ── 2. redirections ──────────────────────────────────────────────────────────

def test_une_redirection_vers_la_boucle_locale_est_refusee(monkeypatch, serveur_local):
    """Le premier saut est une destination DÉCLARÉE (pont local, politique
    connecteur) ; son 302 vise la même machine sur un autre port, non déclaré.
    `requests` suit la redirection lui-même : le saut ouvre sa connexion par le
    transport, qui refuse."""
    port, srv = serveur_local
    srv.redirection = "http://127.0.0.1:9/admin"
    monkeypatch.setenv(egress.ALLOW_VAR, f"pont-local=127.0.0.1:{port}")
    monkeypatch.setattr(egress, "resolved_addresses",
                        _table(**{"pont.exemple.test": ["127.0.0.1"],
                                  "127.0.0.1": ["127.0.0.1"]}))
    with pytest.raises(egress.EgressRefused, match="boucle locale"):
        _connecteur().get(f"http://pont.exemple.test:{port}/", timeout=5)
    assert srv.hotes == [f"pont.exemple.test:{port}"], "seul le premier saut est parti"


def test_une_redirection_vers_un_nom_qui_resout_interne_est_refusee(monkeypatch,
                                                                    serveur_local):
    port, srv = serveur_local
    srv.redirection = "http://deguise.exemple.test/"
    monkeypatch.setenv(egress.ALLOW_VAR, f"pont-local=127.0.0.1:{port}")
    monkeypatch.setattr(egress, "resolved_addresses",
                        _table(**{"pont.exemple.test": ["127.0.0.1"],
                                  "deguise.exemple.test": ["169.254.169.254"]}))
    with pytest.raises(egress.EgressRefused, match="lien-local"):
        _connecteur().get(f"http://pont.exemple.test:{port}/", timeout=5)


# ── 3. l'identité TLS reste celle du nom ─────────────────────────────────────

class _Arret(Exception):
    pass


def test_tls_est_verifie_contre_le_nom_d_origine(monkeypatch):
    """La socket s'ouvre sur l'adresse décidée ; le SNI et la vérification du
    certificat portent le NOM — on n'a pas désactivé la vérification pour joindre
    une adresse littérale."""
    monkeypatch.setattr(egress, "resolved_addresses", _Resolveur([PUBLIQUE]))
    jointes, vu = [], {}

    def ouvrir(adresse, port, *a):
        jointes.append((adresse, port))
        return socket.socketpair()[0]

    def envelopper(**kw):
        vu.update(kw)
        kw["sock"].close()
        raise _Arret()
    monkeypatch.setattr(egress_transport, "_ouvrir", ouvrir)
    monkeypatch.setattr(urllib3.connection, "_ssl_wrap_socket_and_match_hostname",
                        envelopper)
    with pytest.raises(_Arret):
        _agent().get("https://api.exemple.test/v1", timeout=5)
    assert jointes == [(PUBLIQUE, 443)]
    assert vu["server_hostname"] == "api.exemple.test"
    assert vu["assert_hostname"] in (None, "api.exemple.test"), \
        "la vérification du nom n'est ni désactivée ni détournée vers l'adresse"
    assert vu["cert_reqs"] in (None, ssl.CERT_REQUIRED, "CERT_REQUIRED")
    assert vu["ssl_context"] is None or vu["ssl_context"].verify_mode == ssl.CERT_REQUIRED


def test_un_hote_public_est_joint_a_l_adresse_decidee_sous_son_nom(monkeypatch,
                                                                  serveur_local):
    """Le chemin normal : la socket va à l'adresse décidée (ici redirigée vers le
    serveur du banc), la requête porte le nom d'origine en `Host`."""
    port, srv = serveur_local
    monkeypatch.setattr(egress, "resolved_addresses", _Resolveur([PUBLIQUE]))
    jointes = []

    def ouvrir(adresse, p, timeout, *a):
        jointes.append((adresse, p))
        return socket.create_connection(("127.0.0.1", port), timeout)
    monkeypatch.setattr(egress_transport, "_ouvrir", ouvrir)
    r = _agent().get("http://www.exemple.test/page", timeout=5)
    assert r.status_code == 200 and r.text == "ok"
    assert jointes == [(PUBLIQUE, 80)]
    assert srv.hotes == ["www.exemple.test"]


def test_une_adresse_injoignable_cede_la_place_a_la_suivante(monkeypatch, serveur_local):
    """Toutes les adresses sont décidées, puis essayées dans l'ordre (IPv4 d'abord) :
    un hôte double pile dont une famille est injoignable ne doit pas échouer."""
    port, _ = serveur_local
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE_V6, PUBLIQUE]))
    essais = []

    def ouvrir(adresse, p, timeout, *a):
        essais.append(adresse)
        if adresse == PUBLIQUE:
            raise OSError("réseau injoignable")
        return socket.create_connection(("127.0.0.1", port), timeout)
    monkeypatch.setattr(egress_transport, "_ouvrir", ouvrir)
    assert _agent().get("http://double.exemple.test/", timeout=5).status_code == 200
    assert essais == [PUBLIQUE, PUBLIQUE_V6]


# ── 4. les politiques ────────────────────────────────────────────────────────

def test_une_exception_declaree_passe_pour_un_connecteur(monkeypatch, serveur_local):
    port, srv = serveur_local
    monkeypatch.setenv(egress.ALLOW_VAR, f"pont-local=127.0.0.1:{port}")
    monkeypatch.setattr(egress, "resolved_addresses",
                        _table(**{"pont.exemple.test": ["127.0.0.1"]}))
    r = _connecteur().get(f"http://pont.exemple.test:{port}/", timeout=5)
    assert r.status_code == 200
    assert srv.hotes == [f"pont.exemple.test:{port}"]


def test_une_exception_declaree_ne_vaut_pas_pour_une_url_d_agent(monkeypatch,
                                                                 serveur_local):
    port, srv = serveur_local
    monkeypatch.setenv(egress.ALLOW_VAR, f"pont-local=127.0.0.1:{port}")
    monkeypatch.setattr(egress, "resolved_addresses",
                        _table(**{"pont.exemple.test": ["127.0.0.1"]}))
    with pytest.raises(egress.EgressRefused, match="internet public"):
        _agent().get(f"http://pont.exemple.test:{port}/", timeout=5)
    assert srv.hotes == []


def test_un_proxy_est_refuse_et_l_environnement_ignore(monkeypatch, aucune_socket):
    """Derrière un proxy, c'est lui qui résout : l'épinglage ne verrait plus rien."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.exemple.test:3128")
    s = _agent()
    assert s.trust_env is False
    s.proxies = {"https": "http://proxy.exemple.test:3128"}
    with pytest.raises(egress.EgressRefused, match="proxy"):
        s.get("https://api.exemple.test/", timeout=5)


def test_l_adaptateur_survit_a_un_pickle():
    import pickle
    s = pickle.loads(pickle.dumps(_agent()))
    adaptateur = s.get_adapter("https://x.exemple.test/")
    assert isinstance(adaptateur, egress_transport.AdaptateurEpingle)
    pools = adaptateur.poolmanager.pool_classes_by_scheme
    assert issubclass(pools["https"].ConnectionCls, egress_transport._Epinglee)


# ── 5. le câblage ────────────────────────────────────────────────────────────

def _monte(s: requests.Session) -> bool:
    return all(isinstance(s.get_adapter(f"{schema}://h.exemple.test/"),
                          egress_transport.AdaptateurEpingle)
               for schema in ("http", "https")) and s.trust_env is False


def test_web_read_refuse_un_rebinding(monkeypatch, aucune_socket):
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import web as W
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE], ["127.0.0.1"]))
    with pytest.raises(McpError, match="boucle locale"):
        W._fetch_http("https://rebond.exemple.test/")


def test_une_source_url_de_fichier_refuse_un_rebinding(monkeypatch, aucune_socket):
    from oto_mcp import file_source as fs
    monkeypatch.setattr(fs.url_perimeter, "perimeter_of_call", lambda: None)
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE], ["10.0.0.7"]))
    with pytest.raises(fs.FileSourceError, match="réseau privé"):
        fs.resolve({"kind": "url", "url": "https://rebond.exemple.test/f.csv"})


def test_l_import_d_un_document_public_refuse_un_rebinding(monkeypatch, aucune_socket):
    from oto_mcp.connectors import api_operations
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE], ["127.0.0.1"]))
    with pytest.raises(api_operations.ImportFailed, match="boucle locale"):
        api_operations.fetch_public_document("https://rebond.exemple.test/openapi.json")


def test_la_carte_api_sort_par_le_transport_epingle_avant_son_auth(monkeypatch):
    """`HttpConnectorClient` crée sa session paresseusement et la confie à l'auth
    (le mode session s'y connecte aussitôt) : la session doit être gardée AVANT —
    c'est la session DONNÉE à oto-core (`session=`) que l'auth configure."""
    from oto_mcp.tools import api as tool_api
    monkeypatch.setattr(egress, "resolved_addresses", _Resolveur([PUBLIQUE]))
    client = tool_api.client_for({"base_url": "https://api.exemple.test",
                                  "auth_mode": "bearer", "token": "t"},
                                 {"X-Constante": "1"})
    s = client._ready()
    assert _monte(s)
    assert s.headers["Authorization"] == "Bearer t", "l'auth a configuré CETTE session"
    assert s.headers["X-Constante"] == "1", "les en-têtes constants aussi"
    assert client._ready() is s


def test_la_carte_api_refuse_un_rebinding_a_l_appel(monkeypatch, aucune_socket):
    from oto_mcp.tools import api as tool_api
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE], ["127.0.0.1"]))
    client = tool_api.client_for({"base_url": "https://api.exemple.test",
                                  "auth_mode": "bearer", "token": "t"})
    with pytest.raises(egress.EgressRefused, match="boucle locale"):
        client.get("/v1/x")


@pytest.mark.parametrize("connecteur,fields,config", [
    ("n8n",     {"api_key": "k", "base_url": "https://n8n.exemple.test"}, None),
    ("make",    {"api_token": "t", "base_url": "https://make.exemple.test/api/v2"}, None),
    ("git" "hub", {"token": "t", "base_url": "https://ghe.exemple.test/api/v3"}, None),
    ("posthog", {"api_key": "k"}, {"host": "https://ph.exemple.test"}),
    ("lucca",   {"api_key": "k", "domain": "acme"}, None),
])
def test_la_sonde_d_un_connecteur_a_hote_libre_refuse_un_rebinding(
        monkeypatch, aucune_socket, connecteur, fields, config):
    """Public au contrôle, interne à la connexion : la sonde est refusée par le
    transport, sans qu'aucune socket ne s'ouvre. Un connecteur dont le client ne
    passerait pas par sa session gardée ouvrirait une socket — `aucune_socket`
    fait alors échouer le banc."""
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE], ["127.0.0.1"]))
    monkeypatch.delenv(egress.ALLOW_VAR, raising=False)
    sonde = importlib.import_module(f"oto_mcp.tools.{connecteur}")._verify
    with pytest.raises(egress.EgressRefused, match="boucle locale"):
        sonde(fields, config)


def test_le_client_3cx_porte_une_session_gardee(monkeypatch):
    """3CX obtient son jeton par un appel de module dans la lib (reste nommé dans
    `docs/connector-vault.md`) ; ses appels d'API, eux, passent par la session."""
    from oto_mcp.tools import threecx
    monkeypatch.setattr(egress, "resolved_addresses", _Resolveur([PUBLIQUE]))
    monkeypatch.setattr(threecx.access, "resolve_credential_fields", lambda name: {
        "base_url": "https://pbx.exemple.test", "auth_mode": "api_client",
        "client_id": "c", "client_secret": "s"})
    assert _monte(threecx.client().session)


def test_l_echange_du_code_salesforce_sort_par_une_session_gardee(monkeypatch, aucune_socket):
    """`login_url` vient de la carte d'un administrateur et reçoit le code ET le secret
    de l'application. La carte a été contrôlée à la pose ; ce qui fait foi à l'échange
    est la résolution AU MOMENT de la connexion (ici interne) : refusé, sans socket."""
    from oto_mcp.auth import salesforce
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur(["127.0.0.1"]))
    monkeypatch.delenv(egress.ALLOW_VAR, raising=False)
    with pytest.raises(RuntimeError, match="boucle locale"):
        salesforce.exchange_code("c", "id", "secret", "https://acme.my.exemple.test", "v")


def test_l_echange_partage_n_utilise_la_session_que_si_on_la_donne(monkeypatch):
    """Le défaut des autres fournisseurs (hôte fixe) ne bouge pas : `requests.post`."""
    from oto_mcp.auth import flow as oauth_flow

    class _Rep:
        status_code = 200

        def json(self):
            return {"access_token": "a"}

    vus = []
    monkeypatch.setattr(oauth_flow.requests, "post",
                        lambda *a, **k: vus.append("module") or _Rep())

    class _Session:
        def post(self, *a, **k):
            vus.append("session")
            return _Rep()

    args = dict(code="c", client_id="i", client_secret="s", redirect="https://r.test/cb")
    oauth_flow.exchange_code("https://ex.test/token", **args)
    oauth_flow.exchange_code("https://ex.test/token", session=_Session(), **args)
    assert vus == ["module", "session"]


# ── 6. les clients d'oto-core qui prennent `session=` (oto-core, transport injectable) ──

@pytest.mark.parametrize("connecteur,fields,config,controles", [
    ("lighton", {"api_key": "k", "base_url": "https://lighton.exemple.test"}, None, 1),
    ("salesforce", {"client_id": "c", "client_secret": "s", "refresh_token": "r",
                    "login_url": "https://acme.my.exemple.test"}, None, 1),
    ("wordpress", {"site_url": "https://blog.exemple.test", "username": "u",
                   "application_password": "p"}, None, 1),
    ("threecx", {"base_url": "https://pbx.exemple.test", "auth_mode": "api_client",
                 "client_id": "c", "client_secret": "s"}, None, 1),
])
def test_un_client_a_appels_de_module_sort_desormais_par_le_transport(
        monkeypatch, aucune_socket, connecteur, fields, config, controles):
    """Public au contrôle, interne à la connexion : refusé par le transport, jetons
    compris (Salesforce sur `login_url`, 3CX), sans socket ouverte."""
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE], ["127.0.0.1"], n=controles))
    # 3CX fait aussi son propre contrôle d'adresse publique, par la résolution du
    # système : vue publique ici. Le transport, lui, passe par la couture d'egress.
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIQUE, 443))])
    monkeypatch.delenv(egress.ALLOW_VAR, raising=False)
    sonde = importlib.import_module(f"oto_mcp.tools.{connecteur}")._verify
    with pytest.raises(Exception, match="boucle locale"):
        sonde(fields, config)


def test_le_jeton_oauth2_de_la_carte_api_sort_par_le_transport(monkeypatch, aucune_socket):
    """`token_url` reçoit le secret client : sa requête sort par sa propre session
    gardée, distincte de celle des appels."""
    from oto_mcp.tools import api as tool_api
    monkeypatch.setattr(egress, "resolved_addresses",
                        _Resolveur([PUBLIQUE], ["127.0.0.1"], n=2))
    client = tool_api.client_for({"base_url": "https://api.exemple.test",
                                  "auth_mode": "oauth2",
                                  "token_url": "https://jeton.exemple.test/token",
                                  "client_id": "c", "client_secret": "s"})
    with pytest.raises(egress.EgressRefused, match="token_url"):
        client.get("/v1/x")
