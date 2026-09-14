"""Façade DCR sur le host d'un TENANT — enregistrer chez lui, ou dire qu'on ne l'a
pas fait.

Mesuré le 2026-09-08 en production : `POST <domaine MCP du partenaire>/oauth/register` rendait
**201 avec un client_id sans avoir rien enregistré**. `_register_redirects` n'était
appelé que sur le host de la plateforme, et un tenant porteur d'un `oauth_client_id`
n'avait même pas d'avertissement : le client recevait un succès, puis
`oidc.invalid_redirect_uri` à l'`/authorize`, sans indice (oto-backend#909).

Aucun test ne couvrait cette branche — la fixture de `test_oauth_facade_dcr.py`
neutralise `tenant_for_host` en `None`, donc ses quatre cas décrivent uniquement le
host de la plateforme. Ce qui est gardé ici :

1. **sans accès d'administration, la façade REFUSE et nomme sa destination** — ce qui
   manque et à qui le demander. Un tenant dont nous n'hébergerions pas l'annuaire
   retombe là, définitivement : c'est le geste qui vaut seul ;
2. **avec, elle enregistre dans l'annuaire DU TENANT** — jeton pris sur son endpoint
   d'ADMINISTRATION, appels `/api` sur son endpoint PRINCIPAL (l'inverse rend
   `401 aud check_failed`), et cache de jeton **clefé par annuaire**. Un cache partagé
   servirait à l'un le jeton de l'autre : au mieux des refus intermittents, au pire
   une écriture dirigée vers le mauvais annuaire.
"""
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import tenancy
from oto_mcp.auth import facade
from oto_mcp.auth.facade import make_routes

_HOST = "mcp.acme.test"
_MGMT = {"token_endpoint": "https://admin.acme.test",
         "api_endpoint": "https://auth.acme.test",
         "credential": "LOGTO_ACME_MGMT"}
_CORPS = {"redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"]}


def _entry(**surcharge):
    """L'entrée de registre d'un tenant, construite par le VRAI chemin (`build`) :
    une déclaration de test qui contournerait la normalisation ne prouverait rien de
    ce que la base produit."""
    row = {"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc",
           "hosts": [_HOST], "oauth_client_id": "app-acme", "logto_mgmt": dict(_MGMT)}
    row.update(surcharge)
    registre = tenancy.IssuerRegistry(
        tenancy.build("https://auth.oto.ninja/oidc", tenants=[row]))
    return registre.for_host(_HOST)


def _client(monkeypatch, entry):
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.ninja")
    monkeypatch.setattr(facade, "tenant_for_host", lambda host: entry)
    return TestClient(Starlette(routes=make_routes("https://mcp.oto.ninja",
                                                   "app-plateforme")))


@pytest.fixture
def credential_present(monkeypatch):
    monkeypatch.setenv("LOGTO_ACME_MGMT_ID", "cid-de-test")
    monkeypatch.setenv("LOGTO_ACME_MGMT_SECRET", "csec-de-test")


@pytest.fixture
def jamais_enregistre(monkeypatch):
    """Aucun enregistrement ne doit être TENTÉ sur les branches de refus : une
    tentative partirait sur un annuaire choisi par défaut, donc sur le mauvais."""
    monkeypatch.setattr(facade, "_register_redirects", lambda *a, **k: pytest.fail(
        "aucun enregistrement ne doit partir sur cette branche"))


# ── 1. Ce que la façade REFUSE de promettre ──────────────────────────────────

def test_sans_acces_dadministration_la_facade_ne_repond_pas_oui(monkeypatch, caplog,
                                                                jamais_enregistre):
    """Le cas d'aujourd'hui : le tenant porte un client OAuth, nous n'avons aucun
    accès à son annuaire. Rendre 201 annonce une création qui n'aura jamais lieu."""
    client = _client(monkeypatch, _entry(logto_mgmt=None))

    with caplog.at_level("WARNING", logger="oto_mcp.oauth_facade"):
        r = client.post("/oauth/register", json=_CORPS)

    assert r.status_code == 503
    detail = r.json()["error_description"]
    # La destination est NOMMÉE : le tenant à qui demander, et l'application où le
    # rappel doit être posé. Sans ça le client sait seulement qu'on refuse.
    assert "Acme" in detail and "app-acme" in detail
    assert _CORPS["redirect_uris"][0] in detail
    # …et le refus laisse une trace durable côté plateforme (journal + suivi).
    assert "acme" in caplog.text


def test_sans_client_oauth_declare_la_facade_refuse(monkeypatch, jamais_enregistre):
    """Un host de tenant sans `oauth_client_id` : rendre celui de la plateforme
    enverrait le client se présenter chez l'un avec l'identité de l'autre — et
    enregistrer notre app dans SON annuaire viserait une application inexistante."""
    client = _client(monkeypatch, _entry(oauth_client_id=None))

    r = client.post("/oauth/register", json=_CORPS)

    assert r.status_code == 503
    assert "app-plateforme" not in r.text


def test_credential_absent_de_lenvironnement_refuse_sans_le_nommer_au_client(
        monkeypatch, caplog, jamais_enregistre):
    """Déclaré en base, absent du process : le refus doit distinguer les deux causes
    pour NOUS (le journal nomme la variable à injecter) sans livrer notre
    nomenclature interne au client."""
    monkeypatch.delenv("LOGTO_ACME_MGMT_ID", raising=False)
    monkeypatch.delenv("LOGTO_ACME_MGMT_SECRET", raising=False)
    client = _client(monkeypatch, _entry())

    with caplog.at_level("WARNING", logger="oto_mcp.oauth_facade"):
        r = client.post("/oauth/register", json=_CORPS)

    assert r.status_code == 503
    assert "LOGTO_ACME_MGMT" in caplog.text
    assert "LOGTO_ACME_MGMT" not in r.text


# ── 2. Ce qu'elle FAIT quand elle le peut ────────────────────────────────────

def test_enregistre_dans_lannuaire_du_tenant(monkeypatch, credential_present):
    vus = {}
    monkeypatch.setattr(facade, "_register_redirects",
                        lambda app_id, redirects, directory=None: vus.update(
                            app=app_id, r=list(redirects), d=directory))
    client = _client(monkeypatch, _entry())

    r = client.post("/oauth/register", json=_CORPS)

    assert r.status_code == 201
    assert r.json()["client_id"] == "app-acme"
    # L'app visée est celle du tenant, et l'annuaire visé est le SIEN.
    assert vus["app"] == "app-acme" and vus["r"] == _CORPS["redirect_uris"]
    assert vus["d"].api_endpoint == "https://auth.acme.test"
    assert vus["d"].token_endpoint == "https://admin.acme.test"


def test_le_jeton_se_prend_sur_ladmin_et_les_appels_sur_lapi(monkeypatch,
                                                             credential_present):
    """Chez Logto, le jeton de management s'obtient sur l'endpoint d'ADMINISTRATION
    et les appels `/api` vont sur l'endpoint PRINCIPAL. L'inverse rend un refus
    d'audience, très loin de sa cause."""
    appels = []
    monkeypatch.setattr(facade, "_mgmt_toks", {})

    class _Rep:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    def _post(url, **kw):
        appels.append(("POST", url))
        return _Rep({"access_token": "TOK-ACME", "expires_in": 3600})

    def _get(url, **kw):
        appels.append(("GET", url))
        return _Rep({"oidcClientMetadata": {"redirectUris": []},
                     "customClientMetadata": {}})

    def _patch(url, **kw):
        appels.append(("PATCH", url))
        return _Rep({})

    monkeypatch.setattr("requests.post", _post)
    monkeypatch.setattr("requests.get", _get)
    monkeypatch.setattr("requests.patch", _patch)

    facade._register_redirects("app-acme", _CORPS["redirect_uris"],
                               facade.directory_for_tenant(_entry()))

    assert ("POST", "https://admin.acme.test/oidc/token") in appels
    assert ("GET", "https://auth.acme.test/api/applications/app-acme") in appels
    assert ("PATCH", "https://auth.acme.test/api/applications/app-acme") in appels


def test_le_jeton_dun_annuaire_ne_sert_jamais_a_lautre(monkeypatch,
                                                       credential_present):
    """Le cache de jeton est CLEFÉ par annuaire. Partagé, il servirait à l'un le
    jeton de l'autre : au mieux un refus intermittent, au pire une écriture dirigée
    vers le mauvais annuaire — et le coffre du partenaire est derrière."""
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.ninja")
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_ID", "cid-oto")
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_SECRET", "csec-oto")
    monkeypatch.setattr(facade, "_mgmt_toks", {})
    vus = []

    class _Rep:
        def __init__(self, tok):
            self.tok = tok

        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": self.tok, "expires_in": 3600}

    def _post(url, **kw):
        vus.append(url)
        return _Rep("TOK-" + url.split("//")[1].split(".")[0])

    monkeypatch.setattr("requests.post", _post)

    primaire = facade._mgmt_token()
    tenant = facade._mgmt_token(facade.directory_for_tenant(_entry()))

    assert primaire == "TOK-auth" and tenant == "TOK-admin"
    assert primaire != tenant
    assert vus == ["https://auth.oto.ninja/oidc/token",
                   "https://admin.acme.test/oidc/token"]
    # …et chacun garde SON jeton en cache : rejouer ne repart pas sur le réseau,
    # et surtout ne rend pas celui du voisin.
    assert facade._mgmt_token() == "TOK-auth"
    assert len(vus) == 2


# ── 3. Une déclaration illisible ne se charge pas à moitié ───────────────────

@pytest.mark.parametrize("mgmt", [
    {"token_endpoint": "https://admin.acme.test", "credential": "LOGTO_ACME_MGMT"},
    {"token_endpoint": "http://admin.acme.test", "api_endpoint": "https://auth.acme.test",
     "credential": "LOGTO_ACME_MGMT"},
    {"token_endpoint": "https://admin.acme.test", "api_endpoint": "https://auth.acme.test",
     "credential": "logto_acme_mgmt; rm -rf"},
    "pas un objet",
])
def test_une_declaration_incomplete_est_refusee_pas_devinee(mgmt):
    """Compléter une déclaration à moitié lue, c'est choisir un endpoint à la place
    du partenaire. Le registre ne la charge pas — et l'appelant refuse en le disant,
    ce qui est exactement le comportement d'un annuaire non administrable."""
    assert _entry(logto_mgmt=mgmt).logto_mgmt is None
