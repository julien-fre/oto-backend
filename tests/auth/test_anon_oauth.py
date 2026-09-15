"""Le shim OAuth anonyme refuse ce qu'il doit refuser (ADR 0032).

`oto_mcp/auth/anon.py` satisfait le flux OAuth qu'exigent les connecteurs custom de
claude.ai et de Mistral pour un serveur MCP qui, lui, ne demande AUCUNE auth : sans ces
routes, la découverte et l'enregistrement dynamique renvoient 404 et le client échoue à
s'inscrire. Le shim AUTO-APPROUVE — aucun écran de login, aucun compte créé — et délivre
un jeton opaque sans privilège que l'app anonyme ne vérifie jamais.

**Ce module était à 0 % de couverture au 15/09/2026**, seul maillon non testé d'une chaîne
qui l'est (le dispatch par hôte à 77 %, la page partagée à 91 %, les opt-ins d'exposition
à 100 %). Rien ne s'exécutait : ni la garde anti-open-redirect, ni PKCE, ni l'usage unique
du code. Ce banc tient les trois, et il les tient par le REFUS — un shim qui auto-approuve
n'a que ses refus pour garde.

Le module nomme lui-même son seul risque réel : devenir un redirecteur ouvert. C'est donc
par là qu'on commence, et on vérifie à chaque refus qu'AUCUN code n'a été émis — un 400
qui laisserait un code derrière lui rendrait la garde décorative.
"""
from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp.auth import anon

# Le seul rappel accepté, tel que `oto_mcp/auth/facade.py` le déclare : schéma https,
# hôte dans l'allowlist, chemin de rappel exact.
_OK = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(autouse=True)
def _store_propre():
    """`_CODES` est un dictionnaire de module : sans ce nettoyage, un test lirait les
    codes d'un autre et « usage unique » passerait pour la mauvaise raison."""
    anon._CODES.clear()
    yield
    anon._CODES.clear()


@pytest.fixture
def client():
    return TestClient(Starlette(routes=anon.make_routes()))


def _verifier_et_challenge(verifier: str = "un-verifier-assez-long-pour-etre-credible"):
    d = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(d).decode().rstrip("=")


def _autorise(client, **params):
    return client.get("/authorize", params=params, follow_redirects=False)


# ── la garde qui compte : ne pas devenir un redirecteur ouvert ────────────────

@pytest.mark.parametrize("uri, pourquoi", [
    ("https://evil.example/api/mcp/auth_callback", "hôte inconnu, bon chemin"),
    ("https://claude.ai.evil.example/api/mcp/auth_callback", "l'hôte autorisé en PRÉFIXE d'un autre"),
    ("https://evilclaude.ai/api/mcp/auth_callback", "l'hôte autorisé en SUFFIXE d'un autre"),
    ("http://claude.ai/api/mcp/auth_callback", "bon hôte, bon chemin, mais pas https"),
    ("https://claude.ai/ailleurs", "bon hôte, chemin qui n'est pas le rappel"),
    ("//evil.example/api/mcp/auth_callback", "URI sans schéma — le navigateur y verrait un absolu"),
    ("javascript:alert(1)", "schéma qui n'est pas du transport"),
    ("", "rappel absent"),
])
def test_un_rappel_hors_allowlist_est_refuse_sans_laisser_de_code(client, uri, pourquoi):
    r = _autorise(client, redirect_uri=uri, state="s1", code_challenge="c1")
    assert r.status_code == 400, pourquoi
    assert r.json()["error"] == "invalid_redirect_uri"
    # Le refus doit être SEC : pas de 302, et rien de consommable laissé derrière.
    assert "location" not in {k.lower() for k in r.headers}
    assert anon._CODES == {}, "un code émis malgré le refus rendrait la garde décorative"


def test_le_rappel_declare_est_accepte_et_rend_le_state_intact(client):
    # Un `state` qui contient des caractères à encoder : c'est la protection CSRF du
    # client, le shim doit le rendre tel qu'il l'a reçu, pas une version recodée.
    state = "s/a+b==~x"
    r = _autorise(client, redirect_uri=_OK, state=state, code_challenge="c1")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == [state]
    assert q["code"][0] in anon._CODES


def test_un_rappel_qui_porte_deja_une_requete_garde_la_sienne(client):
    # `authorize` choisit `?` ou `&` selon le rappel : se tromper collerait le code dans
    # la valeur du dernier paramètre du client au lieu d'en ajouter un.
    r = _autorise(client, redirect_uri=_OK + "?deja=1", state="s", code_challenge="c1")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["deja"] == ["1"] and len(q["code"]) == 1


# ── PKCE : le défi posé à l'autorisation lie l'échange ────────────────────────

def test_un_verifier_qui_ne_repond_pas_au_defi_est_refuse(client):
    _, challenge = _verifier_et_challenge()
    code = parse_qs(urlparse(_autorise(
        client, redirect_uri=_OK, code_challenge=challenge).headers["location"]).query)["code"][0]

    r = client.post("/token", data={"grant_type": "authorization_code",
                                    "code": code, "code_verifier": "pas-le-bon"})
    assert r.status_code == 400
    assert r.json()["error_description"] == "PKCE mismatch"


def test_le_verifier_qui_repond_au_defi_obtient_un_jeton(client):
    verifier, challenge = _verifier_et_challenge()
    code = parse_qs(urlparse(_autorise(
        client, redirect_uri=_OK, code_challenge=challenge).headers["location"]).query)["code"][0]

    r = client.post("/token", data={"grant_type": "authorization_code",
                                    "code": code, "code_verifier": verifier})
    assert r.status_code == 200
    assert r.json()["token_type"] == "Bearer"


# ── le code : une seule fois, et pas éternellement ───────────────────────────

def test_un_code_ne_sert_qu_une_fois(client):
    code = parse_qs(urlparse(_autorise(
        client, redirect_uri=_OK).headers["location"]).query)["code"][0]
    donnees = {"grant_type": "authorization_code", "code": code}

    assert client.post("/token", data=donnees).status_code == 200
    rejoue = client.post("/token", data=donnees)
    assert rejoue.status_code == 400 and rejoue.json()["error"] == "invalid_grant"


def test_un_code_perime_est_refuse(client):
    code = parse_qs(urlparse(_autorise(
        client, redirect_uri=_OK).headers["location"]).query)["code"][0]
    # On vieillit l'entrée plutôt que d'attendre le TTL : c'est l'échéance qu'on teste,
    # pas la patience de la suite.
    challenge, uri, _ = anon._CODES[code]
    anon._CODES[code] = (challenge, uri, time.time() - 1)

    r = client.post("/token", data={"grant_type": "authorization_code", "code": code})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_l_echeance_posee_est_courte(client):
    """Le refus d'un code échu ne vaut que si l'échéance tombe vite.

    `test_un_code_perime_est_refuse` vieillit l'entrée à la main : il prouve que le
    serveur COMPARE, pas que le délai posé soit raisonnable. Vérifié : en allongeant
    `_CODE_TTL` à un milliard de secondes, ce banc restait entièrement vert sans cette
    assertion-ci. Un code d'autorisation qui vit longtemps élargit d'autant la fenêtre
    pendant laquelle un code intercepté reste échangeable.
    """
    code = parse_qs(urlparse(_autorise(
        client, redirect_uri=_OK).headers["location"]).query)["code"][0]
    _, _, echeance = anon._CODES[code]
    assert echeance - time.time() <= 600, "un code d'autorisation ne doit pas vivre 10 min"


def test_un_code_inconnu_est_refuse(client):
    r = client.post("/token", data={"grant_type": "authorization_code", "code": "jamais-emis"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_un_type_de_grant_non_prevu_est_refuse(client):
    r = client.post("/token", data={"grant_type": "client_credentials"})
    assert r.status_code == 400 and r.json()["error"] == "unsupported_grant_type"


# ── ce que le jeton délivré N'OUVRE PAS ──────────────────────────────────────

def test_le_jeton_anonyme_ne_porte_pas_la_marque_des_jetons_opaques_d_oto(client):
    """La propriété de sécurité centrale du shim : il délivre un jeton SANS privilège.

    La face authentifiée ne reconnaît un jeton opaque d'Oto qu'au préfixe `oto_`
    (`oto_mcp/server.py`, sélection par jeton). Un jeton anonyme qui porterait ce préfixe
    entrerait dans cette sélection au lieu d'être ignoré — c'est le seul chemin par lequel
    ce shim, qui auto-approuve sans le moindre login, pourrait ouvrir quoi que ce soit.
    """
    jeton = client.post("/token", data={"grant_type": "refresh_token"}).json()
    for cle in ("access_token", "refresh_token"):
        assert not jeton[cle].startswith("oto_"), cle
        assert jeton[cle].startswith("anon-"), cle
    assert jeton["scope"] == "mcp"


# ── ce que la découverte annonce ─────────────────────────────────────────────

def test_la_decouverte_se_nomme_par_l_hote_servi(client):
    # Le sous-domaine d'un projet publié sert sa PROPRE adresse : un issuer figé
    # renverrait le client vers un autre hôte que celui où il est entré.
    r = client.get("/.well-known/oauth-authorization-server",
                   headers={"host": "un-projet.mcp.oto.cx"})
    m = r.json()
    assert m["issuer"] == "https://un-projet.mcp.oto.cx"
    assert m["authorization_endpoint"] == "https://un-projet.mcp.oto.cx/authorize"
    assert m["code_challenge_methods_supported"] == ["S256"]


def test_la_ressource_protegee_designe_le_point_mcp_du_meme_hote(client):
    r = client.get("/.well-known/oauth-protected-resource",
                   headers={"host": "un-projet.mcp.oto.cx"})
    assert r.json()["resource"] == "https://un-projet.mcp.oto.cx/mcp"


def test_la_decouverte_oidc_ajoute_ce_que_les_clients_openid_attendent(client):
    r = client.get("/.well-known/openid-configuration",
                   headers={"host": "un-projet.mcp.oto.cx"})
    m = r.json()
    assert m["issuer"] == "https://un-projet.mcp.oto.cx"
    assert m["subject_types_supported"] == ["public"]


@pytest.mark.parametrize("route", ["/register", "/token"])
def test_le_preflight_recoit_les_entetes_cors(client, route):
    # Ces deux routes sont appelées depuis un navigateur : sans réponse au préflight,
    # le client n'émet jamais la vraie requête et l'échec est muet côté serveur.
    r = client.options(route)
    assert r.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in r.headers}


def test_le_magasin_de_codes_reste_borne(client, monkeypatch):
    """Le shim auto-approuve : n'importe qui peut lui faire poser des codes en rafale.

    Le magasin est un dictionnaire de module, mono-process, purgé seulement à l'échange
    ou par ce plafond — sans lui, une rafale de `/authorize` le ferait croître sans fin.
    On abaisse le plafond pour éprouver le MÉCANISME d'éviction, pas sa valeur ; la
    valeur, elle, est vérifiée finie juste en dessous.
    """
    monkeypatch.setattr(anon, "_CAP", 3)
    for _ in range(8):
        _autorise(client, redirect_uri=_OK, code_challenge="c")
    assert len(anon._CODES) <= 3


def test_le_plafond_du_magasin_est_fini():
    assert 0 < anon._CAP < 10**6


def test_un_enregistrement_au_corps_illisible_est_refuse_en_le_nommant(client):
    # RFC 7591 §3.1 attend un document JSON ; deviner à la place du client enregistrerait
    # des `redirect_uris` qu'il n'a pas écrits.
    r = client.post("/register", content=b"{pas du json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata"


def test_un_enregistrement_sans_corps_garde_le_comportement_d_avant(client):
    r = client.post("/register")
    assert r.status_code == 201
    assert r.json()["client_id"] == anon._ANON_CLIENT_ID
    assert r.json()["token_endpoint_auth_method"] == "none"
