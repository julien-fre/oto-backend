"""Façade DCR — les rappels EXACTS qu'un tenant déclare, et rien de plus large.

Le besoin : un client hébergé dont le tableau de bord pose SON propre rappel, un par
instance (`https://<id>.agents.example.test/api/mcp/oauth/callback/<serveur>`), veut se
connecter au domaine MCP d'UN tenant. Le cas de départ est Hermes Cloud (PR externe
#1026, dont la FORME du rappel et les cas de voisins refusés sont repris ici).

Ce qui est REFUSÉ, et pourquoi : un joker `*.agents.example.test` dans la liste globale
(`_redirect_ok`). `POST /oauth/register` n'est pas authentifié et pose le rappel demandé
dans l'application partagée : avec un joker, n'importe quel client du produit hébergé
(sous-domaine en libre-service) enregistre le sien, envoie à un utilisateur un lien
d'autorisation, et reçoit le code — le PKCE n'y change rien, c'est lui qui ouvre le flux.
Les hôtes fixes de la liste globale (chatgpt.com, callback.mistral.ai) n'ont pas ce défaut :
c'est le serveur du fournisseur qui reçoit le code. Et la liste globale vaut pour TOUS les
hosts, `mcp.oto.cx` compris, pour le bénéfice d'un seul tenant.

Ce qui est gardé ici :
1. la déclaration est une liste d'URLs EXACTES **par tenant** (`logto_mgmt.redirect_uris`) ;
2. elle ne vaut que sur les hosts de CE tenant — pas sur la plateforme, pas sur un autre
   tenant, pas sur les endpoints anonymes ;
3. la DCR et l'autorisation relayée appliquent la MÊME garde (`redirect_autorise`) ;
4. une entrée qui n'est pas une URL https complète est écartée au chargement, en alertant ;
5. `_redirect_ok` reste globale et inchangée : aucun rappel de tenant n'y entre.

Données FICTIVES (domaines `.test` / `.example.test`), jamais un identifiant d'instance réel.
"""
from __future__ import annotations

from urllib.parse import quote, urlparse

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import tenancy
from oto_mcp.auth import anon, facade, relay

_ACME = "mcp.acme.test"
_AUTRE = "mcp.autre.test"
_PLATEFORME = "mcp.oto.cx"
_INST_A = "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv"
_INST_B = "https://inst-b.agents.example.test/api/mcp/oauth/callback/srv"
_STABLE = "https://chatgpt.com/connector_platform_oauth_redirect"
_SECRET = b"secret-de-banc"

_MGMT_ACME = {"token_endpoint": "https://admin.acme.test",
              "api_endpoint": "https://auth.acme.test",
              "credential": "LOGTO_ACME_MGMT"}
_MGMT_AUTRE = {"token_endpoint": "https://admin.autre.test",
               "api_endpoint": "https://auth.autre.test",
               "credential": "LOGTO_AUTRE_MGMT"}


def _lignes(declares=(_INST_A,)):
    """Deux tenants sur des hosts distincts : `acme` déclare des rappels, `autre` non.
    Construits par le VRAI chemin (`tenancy.build`) — une entrée fabriquée à la main
    contournerait la normalisation, donc ne prouverait rien de ce que la base produit."""
    acme = dict(_MGMT_ACME)
    if declares is not None:
        acme["redirect_uris"] = list(declares)
    return [
        {"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc",
         "hosts": [_ACME], "oauth_client_id": "app-acme", "logto_mgmt": acme},
        {"slug": "autre", "name": "Autre", "issuer": "https://auth.autre.test/oidc",
         "hosts": [_AUTRE], "oauth_client_id": "app-autre", "logto_mgmt": dict(_MGMT_AUTRE)},
    ]


def _registre(declares=(_INST_A,)):
    return tenancy.IssuerRegistry(
        tenancy.build("https://auth.oto.ninja/oidc", tenants=_lignes(declares)))


class _Annuaire:
    """Ce que Logto reçoit : les rappels que la façade y POSE, par application."""

    def __init__(self):
        self.poses: list = []
        self.rappels: list = []

    def enregistrer(self, app_id, uris, directory=None, *, cors_uris=None):
        self.poses.append((app_id, list(uris)))
        self.rappels += [u for u in uris if u not in self.rappels]

    def relire(self, app_id, directory=None):
        return list(self.rappels)


@pytest.fixture
def annuaire(monkeypatch):
    a = _Annuaire()
    monkeypatch.setattr(facade, "_register_redirects", a.enregistrer)
    monkeypatch.setattr(facade, "_redirect_uris", a.relire)
    relay._rappels.clear()
    relay._seaux.clear()
    yield a
    relay._rappels.clear()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.ninja")
    monkeypatch.setenv("LOGTO_PUBLIC_ENDPOINT", "https://auth.oto.cx")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", _SECRET.decode())
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_ID", "cid")
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_SECRET", "csec")
    for nom in ("ACME", "AUTRE"):
        monkeypatch.setenv(f"LOGTO_{nom}_MGMT_ID", "cid")
        monkeypatch.setenv(f"LOGTO_{nom}_MGMT_SECRET", "csec")
    monkeypatch.setenv("OTO_MCP_OAUTH_RELAY_HOSTS", ",".join((_ACME, _AUTRE, _PLATEFORME)))


def _installer(monkeypatch, declares=(_INST_A,)):
    avant = tenancy.current()
    tenancy.install(_registre(declares))
    return avant


@pytest.fixture
def client(monkeypatch, annuaire, env):
    avant = _installer(monkeypatch)
    yield TestClient(Starlette(routes=facade.make_routes(f"https://{_PLATEFORME}",
                                                          "app-plateforme")))
    tenancy.install(avant)


def _inscrire(client, uri, host=_ACME):
    return client.post("/oauth/register", json={"redirect_uris": [uri]},
                       headers={"host": host})


# ── 1. Ce qui passe : l'URL EXACTE déclarée, sur le host de CE tenant ─────────

def test_lurl_exacte_declaree_senregistre_chez_le_tenant(client, annuaire):
    r = _inscrire(client, _INST_A)

    assert r.status_code == 201
    assert r.json()["client_id"] == "app-acme"
    # Posée dans l'application DU TENANT — jamais dans l'application partagée.
    assert annuaire.poses[0][0] == "app-acme" and _INST_A in annuaire.poses[0][1]
    assert all(app != "app-plateforme" for app, _ in annuaire.poses)


def test_le_rappel_declare_se_combine_avec_la_liste_globale(client, annuaire):
    r = client.post("/oauth/register", json={"redirect_uris": [_INST_A, _STABLE]},
                    headers={"host": _ACME})
    assert r.status_code == 201


# ── 2. Ce qui ne passe JAMAIS : la même URL ailleurs ──────────────────────────

def test_la_meme_url_est_refusee_sur_le_host_de_la_plateforme(client, annuaire):
    r = _inscrire(client, _INST_A, host=_PLATEFORME)

    assert (r.status_code, r.json()["error"]) == (400, "invalid_redirect_uri")
    assert annuaire.poses == [], "rien n'est posé chez nous : le trou ne s'ouvre pas"


def test_la_meme_url_est_refusee_chez_un_autre_tenant(client, annuaire):
    """`autre` n'a rien déclaré : la liste d'`acme` ne déborde pas sur lui."""
    r = _inscrire(client, _INST_A, host=_AUTRE)

    assert (r.status_code, r.json()["error"]) == (400, "invalid_redirect_uri")
    assert annuaire.poses == []


def test_une_autre_instance_du_meme_domaine_est_refusee_et_nommee(client, annuaire):
    r = _inscrire(client, _INST_B)

    assert (r.status_code, r.json()["error"]) == (400, "invalid_redirect_uri")
    detail = r.json()["error_description"]
    # Le refus dit QUEL rappel et à QUI le demander — un client hébergé ne lit pas nos
    # journaux — sans livrer notre nomenclature interne (slug, colonnes).
    assert _INST_B in detail and "Acme" in detail and "acme" not in detail.replace("Acme", "")
    assert "logto_mgmt" not in detail and "redirect_uris" not in detail
    assert annuaire.poses == []


@pytest.mark.parametrize("uri", [
    # un host VOISIN n'est ni un sous-domaine déclaré, ni un suffixe (cas de la PR #1026)
    "https://evil-agents.example.test/api/mcp/oauth/callback/srv",
    "https://agents.example.test.attaquant.test/api/mcp/oauth/callback/srv",
    "https://inst-a.agents.example.test.attaquant.test/api/mcp/oauth/callback/srv",
    "https://attaquant.test/inst-a.agents.example.test/api/mcp/oauth/callback/srv",
    # le chemin est EXACT : ni segment de plus, ni segment de moins, ni autre serveur
    "https://inst-a.agents.example.test/api/mcp/oauth/callback",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv/suite",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv2",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/autre",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv/",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/../callback/srv",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/%2e%2e/srv",
    # le schéma reste https
    "http://inst-a.agents.example.test/api/mcp/oauth/callback/srv",
    # casse, port, identité, requête, fragment : la comparaison est sur la chaîne
    "https://INST-A.agents.example.test/api/mcp/oauth/callback/srv",
    "https://inst-a.agents.example.test:443/api/mcp/oauth/callback/srv",
    "https://inst-a.agents.example.test@attaquant.test/api/mcp/oauth/callback/srv",
    "https://attaquant.test\\@inst-a.agents.example.test/api/mcp/oauth/callback/srv",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv?x=1",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv#f",
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv ",
])
def test_les_voisins_et_les_variantes_sont_refuses(client, annuaire, uri):
    r = _inscrire(client, uri)

    assert (r.status_code, r.json()["error"]) == (400, "invalid_redirect_uri")
    assert annuaire.poses == []


# ── 3. Sans déclaration, rien ne change ───────────────────────────────────────

def test_un_tenant_sans_liste_garde_exactement_le_comportement_davant(monkeypatch, annuaire,
                                                                      env):
    avant = _installer(monkeypatch, declares=None)
    try:
        c = TestClient(Starlette(routes=facade.make_routes(f"https://{_PLATEFORME}",
                                                           "app-plateforme")))
        refuse = _inscrire(c, _INST_A)
        accepte = _inscrire(c, _STABLE)
    finally:
        tenancy.install(avant)

    assert refuse.status_code == 400
    assert accepte.status_code == 201


def test_la_liste_globale_reste_globale_et_inchangee():
    for uri in (_INST_A, _INST_B, "https://agents.example.test/api/mcp/oauth/callback/srv"):
        assert facade._redirect_ok(uri) is False, "aucun rappel de tenant dans la liste globale"
        assert facade.redirect_autorise(None, uri) is False
    assert facade._redirect_ok(_STABLE) is True


def test_les_endpoints_anonymes_nappellent_que_la_liste_globale():
    """`auth/anon.py` n'a pas de tenant : il doit garder `_redirect_ok`, pas la garde
    étendue — sinon un rappel déclaré chez un tenant s'y ouvrirait aussi."""
    assert anon._redirect_ok is facade._redirect_ok


# ── 4. Le host décide, d'un seul bloc ─────────────────────────────────────────

def test_forger_le_host_dun_tenant_ne_donne_que_ce_que_ce_tenant_a_declare(client, annuaire):
    """`Host` n'est pas authentifié à ce niveau. La liste, l'application et l'annuaire
    viennent pourtant de la MÊME entrée, résolue une fois : l'appelant qui écrit le host
    d'`acme` obtient au plus l'enregistrement, chez `acme`, d'un rappel qu'`acme` a lui-même
    déclaré — jamais un rappel de son choix, jamais chez nous."""
    ok = _inscrire(client, _INST_A, host=_ACME)
    hors_liste = _inscrire(client, _INST_B, host=_ACME)

    assert ok.status_code == 201 and hors_liste.status_code == 400
    assert {app for app, _ in annuaire.poses} == {"app-acme"}


# ── 5. L'autorisation relayée applique la MÊME garde ──────────────────────────

def _demande(rappel, client_id="app-acme"):
    return (f"response_type=code&client_id={quote(client_id, safe='')}"
            f"&redirect_uri={quote(rappel, safe='')}"
            f"&state=etat-du-client&code_challenge=Zx-9_aQ&code_challenge_method=S256")


def _autoriser(client, rappel, host=_ACME, client_id="app-acme"):
    return client.get(f"{relay.AUTHORIZE_PATH}?{_demande(rappel, client_id)}",
                      headers={"host": host}, follow_redirects=False)


def test_lautorisation_relayee_accepte_le_rappel_declare_une_fois_enregistre(client, annuaire):
    assert _inscrire(client, _INST_A).status_code == 201

    r = _autoriser(client, _INST_A)

    assert r.status_code == 302
    assert urlparse(r.headers["location"]).netloc == "auth.acme.test"


def test_lautorisation_refuse_un_rappel_declare_pose_a_la_main_sur_un_autre_host(client,
                                                                                annuaire):
    """Même posé dans l'application par un autre chemin (écriture concurrente, console),
    un rappel qui n'est pas celui de CE host ne s'autorise pas : la garde est refaite ici."""
    annuaire.rappels += [_INST_A, _INST_B]

    sur_la_plateforme = _autoriser(client, _INST_A, host=_PLATEFORME, client_id="app-plateforme")
    chez_autre = _autoriser(client, _INST_A, host=_AUTRE, client_id="app-autre")
    autre_instance = _autoriser(client, _INST_B)

    for r in (sur_la_plateforme, chez_autre, autre_instance):
        assert (r.status_code, r.json()["error"]) == (400, "invalid_request")
        assert "location" not in r.headers


# ── 6. La déclaration se valide au chargement, entrée par entrée ──────────────

@pytest.mark.parametrize("entree", [
    "https://*.agents.example.test/api/mcp/oauth/callback/srv",       # joker
    "https://inst-a.agents.example.test/api/mcp/oauth/callback/*",    # joker de chemin
    "http://inst-a.agents.example.test/api/mcp/oauth/callback/srv",   # pas https
    "https://inst-a.agents.example.test/cb?x=1",                      # requête
    "https://inst-a.agents.example.test/cb#f",                        # fragment
    "https://user@inst-a.agents.example.test/cb",                     # identité
    "https://inst-a.agents.example.test",                             # pas de chemin
    "https://inst-a.agents.example.test/cb\\x",                       # barre oblique inverse
    "https://inst-a.agents.example.test/c b",                         # espace
    "https://inst-a.agents.example.test:99999/cb",                    # port hors bornes
    "agents.example.test/cb",                                         # pas d'URL
    pytest.param("https://" + "a" * 600 + ".test/cb", id="demesuree"),
    pytest.param(42, id="entier"),
    pytest.param(None, id="nul"),
    pytest.param(["https://x.test/cb"], id="liste_imbriquee"),
])
def test_une_entree_douteuse_est_ecartee_et_alertee(monkeypatch, entree):
    alertes = []
    monkeypatch.setattr(tenancy, "_refus", lambda msg, *a: alertes.append(msg % a))
    valide = "https://inst-a.agents.example.test/api/mcp/oauth/callback/srv"

    entry = tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=_lignes(declares=[entree, valide]))).for_host(_ACME)

    assert entry.dcr_redirects == (valide,), "l'entrée douteuse tombe, la valide reste"
    assert alertes and "acme" in alertes[0]


def test_une_declaration_qui_nest_pas_une_liste_ne_declare_rien(monkeypatch):
    alertes = []
    monkeypatch.setattr(tenancy, "_refus", lambda msg, *a: alertes.append(msg % a))
    lignes = _lignes(declares=None)
    lignes[0]["logto_mgmt"]["redirect_uris"] = _INST_A        # une chaîne, pas une liste

    entry = tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc", tenants=lignes)).for_host(_ACME)

    assert entry.dcr_redirects == () and alertes


def test_la_declaration_lue_en_texte_json_se_charge_aussi():
    """`logto_mgmt` arrive de la base en JSONB, parfois encore en texte : même lecture."""
    import json
    lignes = _lignes(declares=None)
    lignes[0]["logto_mgmt"] = json.dumps({**_MGMT_ACME, "redirect_uris": [_INST_A, _INST_A]})

    entry = tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc", tenants=lignes)).for_host(_ACME)

    assert entry.dcr_redirects == (_INST_A,), "dédoublonné, dans l'ordre déclaré"
    assert entry.logto_mgmt == _MGMT_ACME, "les accès d'annuaire gardent leur forme d'avant"


def test_sans_declaration_lentree_ne_porte_aucun_rappel():
    entry = _registre(declares=None).for_host(_ACME)
    assert entry.dcr_redirects == ()
    assert entry.logto_mgmt == _MGMT_ACME
