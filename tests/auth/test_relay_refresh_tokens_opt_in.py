"""Relais d'autorisation — les jetons de rafraîchissement d'un TENANT, en opt-in.

Le défaut mesuré (oto#202, 12-13/09/2026) : Logto ne délivre un jeton de rafraîchissement que
si la demande d'autorisation porte `prompt=consent` avec `offline_access`. Codex envoie
`offline_access` sans `prompt` : sans jeton de rafraîchissement, chaque expiration du jeton
d'accès (3 600 s) laisse le client sans moyen de se rétablir seul (401, découverte, arrêt).
Le correctif d'oto#202 (`authorize_consent`) ne couvre que NOTRE annuaire ; sur un tenant, le
relais passait `consentement=False` en dur, parce que délivrer des jetons de rafraîchissement
à ses utilisateurs est SA décision.

Ce qui est gardé ici :
1. le drapeau est DÉCLARÉ par tenant (`logto_mgmt.refresh_tokens`), désactivé par défaut —
   sans lui, l'autorisation relayée d'un tenant est identique à celle d'avant, à l'octet près ;
2. il ne vaut que sur les hosts de CE tenant : un autre tenant et NOTRE annuaire sont inchangés ;
3. il ne change que `prompt` (l'ajout de `consent`, quand `offline_access` est demandé) :
   aucun scope n'est ajouté, `prompt=none` est laissé tel quel ;
4. il ne se lit que dans la déclaration de l'administrateur : aucun paramètre de requête,
   d'en-tête ou de DCR ne peut l'activer ;
5. une valeur qui n'est pas le booléen JSON `true` est écartée en alertant, drapeau éteint
   (fail-closed) ;
6. la route `/oauth/authorize` de la façade continue de répondre 404 sur un host de tenant.

Données FICTIVES (domaines `.test`), jamais le nom d'un partenaire réel.
"""
from __future__ import annotations

import contextlib
import json
from urllib.parse import parse_qs, quote, urlparse

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import tenancy
from oto_mcp.auth import facade, relay

_ACME = "mcp.acme.test"
_AUTRE = "mcp.autre.test"
_PLATEFORME = "mcp.oto.cx"
_RAPPEL = "https://chatgpt.com/connector_platform_oauth_redirect"
_SECRET = b"secret-de-banc"

_MGMT_ACME = {"token_endpoint": "https://admin.acme.test",
              "api_endpoint": "https://auth.acme.test",
              "credential": "LOGTO_ACME_MGMT"}
_MGMT_AUTRE = {"token_endpoint": "https://admin.autre.test",
               "api_endpoint": "https://auth.autre.test",
               "credential": "LOGTO_AUTRE_MGMT"}


def _lignes(drapeau="__absent__", *, mgmt_acme=None):
    """Deux tenants sur des hosts distincts : `acme` porte (ou non) le drapeau, `autre`
    n'en déclare pas. Construits par le VRAI chemin (`tenancy.build`) — une entrée
    fabriquée à la main contournerait la normalisation, donc ne prouverait rien de ce que
    la base produit."""
    acme = dict(mgmt_acme if mgmt_acme is not None else _MGMT_ACME)
    if drapeau != "__absent__":
        acme["refresh_tokens"] = drapeau
    return [
        {"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc",
         "hosts": [_ACME], "oauth_client_id": "app-acme", "logto_mgmt": acme},
        {"slug": "autre", "name": "Autre", "issuer": "https://auth.autre.test/oidc",
         "hosts": [_AUTRE], "oauth_client_id": "app-autre", "logto_mgmt": dict(_MGMT_AUTRE)},
    ]


def _registre(lignes):
    return tenancy.IssuerRegistry(tenancy.build("https://auth.oto.ninja/oidc", tenants=lignes))


class _Annuaire:
    """Ce que Logto reçoit : les rappels que la façade y POSE, par application."""

    def __init__(self):
        self.rappels = [_RAPPEL]

    def enregistrer(self, app_id, uris, directory=None, *, cors_uris=None):
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


def _client(monkeypatch, lignes):
    avant = tenancy.current()
    tenancy.install(_registre(lignes))
    app = Starlette(routes=facade.make_routes(f"https://{_PLATEFORME}", "app-plateforme"))
    return TestClient(app), avant


@contextlib.contextmanager
def _sous_registre(monkeypatch, lignes):
    """Un client sous un registre donné, puis le registre d'avant. Le registre de tenants
    est UN état global : deux fixtures qui l'installent chacune s'écrasent, donc un test qui
    compare « avec » et « sans » les installe l'un après l'autre, par ici."""
    client, avant = _client(monkeypatch, lignes)
    try:
        yield client
    finally:
        tenancy.install(avant)


@pytest.fixture
def acme_avec_drapeau(monkeypatch, annuaire, env):
    client, avant = _client(monkeypatch, _lignes(True))
    yield client
    tenancy.install(avant)


@pytest.fixture
def acme_sans_drapeau(monkeypatch, annuaire, env):
    client, avant = _client(monkeypatch, _lignes())
    yield client
    tenancy.install(avant)


def _demande(client_id="app-acme", scope="openid+offline_access", extra=""):
    lu = (f"response_type=code&client_id={quote(client_id, safe='')}"
          f"&redirect_uri={quote(_RAPPEL, safe='')}"
          f"&state=etat-du-client&code_challenge=Zx-9_aQ&code_challenge_method=S256")
    if scope:
        lu += f"&scope={scope}"
    return lu + extra


def _autoriser(client, requete, host=_ACME, headers=None):
    return client.get(f"{relay.AUTHORIZE_PATH}?{requete}",
                      headers={"host": host, **(headers or {})}, follow_redirects=False)


def _vers_logto(reponse):
    """La requête d'autorisation telle qu'elle part chez l'annuaire, en paramètres."""
    assert reponse.status_code == 302, reponse.text
    return parse_qs(urlparse(reponse.headers["location"]).query)


# ── 1. Sans drapeau : rien ne change ──────────────────────────────────────────

def test_sans_drapeau_lautorisation_relayee_dun_tenant_ne_porte_pas_consent(acme_sans_drapeau):
    lu = _vers_logto(_autoriser(acme_sans_drapeau, _demande()))

    assert "prompt" not in lu, "le consentement d'un tenant reste SA décision"
    assert lu["scope"] == ["openid offline_access"]


def test_le_defaut_du_registre_est_eteint():
    reg = _registre(_lignes())

    assert reg.for_host(_ACME).refresh_tokens is False
    assert reg.for_host(_AUTRE).refresh_tokens is False
    assert tenancy.TenantIssuer(slug="x", issuer="https://x.test/oidc",
                                jwks_uri="https://x.test/jwks").refresh_tokens is False


@pytest.mark.parametrize("valeur", [False, None])
def test_un_drapeau_explicitement_eteint_ne_declenche_pas_dalerte(monkeypatch, valeur):
    alertes = []
    monkeypatch.setattr(tenancy, "_refus", lambda msg, *a: alertes.append(msg % a))

    entry = _registre(_lignes(valeur)).for_host(_ACME)

    assert entry.refresh_tokens is False and alertes == []


# ── 2. Avec drapeau : `consent`, et rien d'autre ──────────────────────────────

def test_avec_drapeau_lautorisation_relayee_ajoute_consent_et_rien_dautre(acme_avec_drapeau):
    lu = _vers_logto(_autoriser(acme_avec_drapeau, _demande()))

    assert lu["prompt"] == ["consent"]
    assert lu["scope"] == ["openid offline_access"], "aucun scope n'est ajouté ni retiré"
    assert lu["client_id"] == ["app-acme"] and lu["code_challenge_method"] == ["S256"]
    assert lu["redirect_uri"] == ["https://mcp.acme.test/oauth/callback"]


def test_avec_drapeau_sans_offline_access_rien_nest_ajoute(acme_avec_drapeau):
    """Le drapeau ne fabrique aucun droit : un client qui ne demande pas
    `offline_access` suit le parcours d'avant."""
    for scope in ("openid", ""):
        assert "prompt" not in _vers_logto(_autoriser(acme_avec_drapeau,
                                                      _demande(scope=scope)))


def test_avec_drapeau_prompt_none_est_laisse_tel_quel(acme_avec_drapeau):
    """`prompt=none` seul : y ajouter `consent` fabriquerait une combinaison que la norme
    interdit (cf. `authorize_consent`)."""
    lu = _vers_logto(_autoriser(acme_avec_drapeau, _demande(extra="&prompt=none")))

    assert lu["prompt"] == ["none"]


def test_avec_drapeau_un_consent_deja_demande_nest_pas_double(acme_avec_drapeau):
    lu = _vers_logto(_autoriser(acme_avec_drapeau,
                                _demande(extra="&prompt=login+consent")))

    assert lu["prompt"] == ["login consent"]


# ── 3. Le drapeau ne vaut que pour CE tenant ──────────────────────────────────

def test_un_autre_tenant_sans_drapeau_reste_inchange(acme_avec_drapeau, annuaire):
    lu = _vers_logto(_autoriser(acme_avec_drapeau, _demande(client_id="app-autre"),
                                host=_AUTRE))

    assert "prompt" not in lu


def test_notre_annuaire_garde_son_comportement_davant(monkeypatch, annuaire, env):
    """Le drapeau d'`acme` ne touche pas NOTRE annuaire, qui ajoutait déjà `consent`
    (oto#202) : ni plus, ni moins."""
    for lignes in (_lignes(True), _lignes()):
        with _sous_registre(monkeypatch, lignes) as client:
            lu = _vers_logto(_autoriser(client, _demande(client_id="app-plateforme"),
                                        host=_PLATEFORME))
        assert lu["prompt"] == ["consent"]
        assert lu["redirect_uri"] == ["https://mcp.oto.cx/oauth/callback"]


def test_la_route_dautorisation_directe_reste_un_404_sur_un_host_de_tenant(monkeypatch,
                                                                          annuaire, env):
    """`/oauth/authorize` n'existe pas sur un host de tenant, drapeau ou pas : l'opt-in
    passe par le relais, jamais par la route d'oto#202."""
    for lignes in (_lignes(True), _lignes()):
        with _sous_registre(monkeypatch, lignes) as client:
            r = client.get(f"/oauth/authorize?{_demande()}", headers={"host": _ACME},
                           follow_redirects=False)
        assert r.status_code == 404 and r.json()["error"] == "not_found"
        assert "location" not in r.headers


def test_notre_route_directe_garde_le_consentement_sur_notre_host(acme_avec_drapeau):
    r = acme_avec_drapeau.get(f"/oauth/authorize?{_demande(client_id='app-plateforme')}",
                              headers={"host": _PLATEFORME}, follow_redirects=False)

    assert parse_qs(urlparse(r.headers["location"]).query)["prompt"] == ["consent"]


# ── 4. Seule la déclaration de l'administrateur active le drapeau ─────────────

@pytest.mark.parametrize("extra,headers", [
    ("&refresh_tokens=true", {}),
    ("&consent=true", {}),
    ("&prompt_consent=1", {}),
    ("", {"x-refresh-tokens": "true"}),
    ("", {"x-oto-refresh-tokens": "1"}),
])
def test_aucun_parametre_ni_en_tete_ne_peut_lactiver(acme_sans_drapeau, extra, headers):
    r = _autoriser(acme_sans_drapeau, _demande(extra=extra), headers=headers)

    if r.status_code == 302:
        assert "prompt" not in parse_qs(urlparse(r.headers["location"]).query)
    else:
        assert "location" not in r.headers, "un refus ne redirige nulle part"


def test_le_corps_dune_dcr_ne_peut_pas_lactiver(acme_sans_drapeau):
    r = acme_sans_drapeau.post("/oauth/register", headers={"host": _ACME},
                               json={"redirect_uris": [_RAPPEL], "refresh_tokens": True,
                                     "consent": True, "grant_types": ["refresh_token"]})
    assert r.status_code in (201, 400)

    lu = _vers_logto(_autoriser(acme_sans_drapeau, _demande()))
    assert "prompt" not in lu


def test_la_declaration_lue_depuis_du_json_texte_vaut_celle_dun_objet():
    """La base rend parfois le JSONB en chaîne : même lecture que `redirect_uris`."""
    texte = json.dumps({**_MGMT_ACME, "refresh_tokens": True})

    entry = _registre([
        {"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc",
         "hosts": [_ACME], "oauth_client_id": "app-acme", "logto_mgmt": texte}]).for_host(_ACME)

    assert entry.refresh_tokens is True


# ── 5. Fail-closed : une valeur douteuse éteint le drapeau, en alertant ───────

@pytest.mark.parametrize("valeur", [
    "true", "True", "yes", "on", "1", 1, 2, 1.0, [True], {"x": 1}, ["true"], "",
])
def test_une_valeur_qui_nest_pas_le_booleen_true_est_ecartee_et_alertee(monkeypatch, valeur):
    alertes = []
    monkeypatch.setattr(tenancy, "_refus", lambda msg, *a: alertes.append(msg % a))

    entry = _registre(_lignes(valeur)).for_host(_ACME)

    assert entry.refresh_tokens is False, "en cas de doute, aucun jeton de rafraîchissement"
    assert alertes and "acme" in alertes[0] and "refresh_tokens" in alertes[0]


def test_une_valeur_douteuse_nouvre_pas_le_relais(monkeypatch, annuaire, env):
    monkeypatch.setattr(tenancy, "_refus", lambda *a: None)
    client, avant = _client(monkeypatch, _lignes("true"))
    try:
        lu = _vers_logto(_autoriser(client, _demande()))
    finally:
        tenancy.install(avant)

    assert "prompt" not in lu


def test_un_drapeau_sans_declaration_dannuaire_reste_sans_effet(monkeypatch, annuaire, env):
    """Sans accès d'administration (`logto_mgmt` vide), le relais lui-même est refusé
    (503) : le drapeau n'ouvre pas ce qu'il ne peut pas servir."""
    lignes = _lignes(True)
    lignes[0]["logto_mgmt"] = {"refresh_tokens": True}
    monkeypatch.setattr(tenancy, "_refus", lambda *a: None)
    client, avant = _client(monkeypatch, lignes)
    try:
        r = _autoriser(client, _demande())
    finally:
        tenancy.install(avant)

    assert r.status_code == 503 and "location" not in r.headers


def test_le_tenant_primaire_na_jamais_le_drapeau():
    reg = _registre(_lignes(True))

    primaire = reg.get(tenancy.normalize_issuer("https://auth.oto.ninja/oidc"))
    assert primaire is not None and primaire.slug == tenancy.PRIMARY_SLUG
    assert primaire.refresh_tokens is False
