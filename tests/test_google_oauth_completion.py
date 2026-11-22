"""Google CASA, ADA 3.2.2 — le state d'un consentement Google est validé, à usage
unique, et LIÉ à la personne qui a démarré le flux.

Avant ce lot, le callback (`/api/google/oauth/callback`, sans authentification)
échangeait le code et rangeait le jeton sous le `sub` que le state portait. Une URL
de consentement fabriquée par A et ouverte par B rangeait donc le compte Google de B
sous le compte de A (CSRF de liaison de compte). Désormais :

- le callback ne range RIEN — il remet code et state au front, dans le fragment ;
- le front termine avec SON jeton (`me.federation.google.complete`) : le `sub` du
  state doit être le sien, le state se consomme une fois, puis seulement l'échange.

Ces épreuves passent par la VRAIE chaîne : `make_state` réel (signature, audience,
`jti`), le vrai handler du callback, et la vraie route REST de la capacité
(`_datastore_rest.call`). Seuls Google (échange, rangement) et la table des `jti`
consommés sont remplacés.
"""
from __future__ import annotations

import asyncio
import time
import urllib.parse

import pytest
from starlette.requests import Request

from _datastore_rest import call, stub_authz

from oto_mcp import db
from oto_mcp.api import datastore as datastore_routes
from oto_mcp.auth import flow as oauth_flow
from oto_mcp.auth import google as google_oauth

CLE = "me.federation.google.complete"
A, B = "sub-a", "sub-b"
ORG = 42


@pytest.fixture(autouse=True)
def _secret_d_instance(monkeypatch):
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "test-secret")


@pytest.fixture
def google(monkeypatch):
    """Google et le coffre, remplacés : ce qui a été échangé, et pour qui c'est rangé.
    La table des `jti` consommés est un ensemble en mémoire, à la même règle que
    `db.consume_state_jti` (True la première fois, False ensuite)."""
    stub_authz(monkeypatch, org_id=ORG)
    vu = {"echanges": [], "ranges": []}
    consommes: set = set()

    def _consume(aud, jti):
        if (aud, jti) in consommes:
            return False
        consommes.add((aud, jti))
        return True

    def _exchange(code, sub):
        vu["echanges"].append((code, sub))
        return {"access_token": "at", "refresh_token": "rt", "scope": "openid"}

    def _persist(sub, org_id, tokens, **kw):
        vu["ranges"].append((sub, org_id, kw))
        return f"{sub}@example.test"

    monkeypatch.setattr(db, "consume_state_jti", _consume)
    monkeypatch.setattr(google_oauth, "exchange_code", _exchange)
    monkeypatch.setattr(google_oauth, "persist_token", _persist)
    return vu


def _terminer(state: str, *, sub: str, code: str = "c0de"):
    return call(CLE, body={"code": code, "state": state}, sub=sub)


# --- le chemin heureux -------------------------------------------------------

def test_le_consentement_se_range_pour_la_personne_qui_l_a_demarre(google):
    code, out = _terminer(google_oauth.make_state(A, ORG), sub=A)
    assert (code, out) == (200, {"ok": True, "connector": "google",
                                 "account": f"{A}@example.test", "scope": "member"})
    assert google["echanges"] == [("c0de", A)]
    assert google["ranges"] == [(A, ORG, {})], "l'org est celle du DÉPART, portée par le state"


def test_un_compte_partage_garde_la_carte_et_la_cible_du_state(google):
    """Un compte PARTAGÉ est borné par la carte qui a demandé le consentement (revue
    de #1081) : la complétion la passe telle que le state signé la porte."""
    etat = google_oauth.make_state(A, ORG, "", "drive", "group", 9)
    code, out = _terminer(etat, sub=A)
    assert code == 200 and out["connector"] == "drive" and out["scope"] == "group"
    assert google["ranges"] == [(A, ORG, {"scope": "group", "group_id": 9,
                                          "connector": "drive"})]


# --- les refus -----------------------------------------------------------------

def test_un_state_rejoue_est_refuse(google):
    etat = google_oauth.make_state(A, ORG)
    assert _terminer(etat, sub=A)[0] == 200
    code, out = _terminer(etat, sub=A)
    assert (code, out["error"]) == (409, "state_used")
    assert len(google["echanges"]) == 1, "un rejeu n'échange rien"


def test_le_state_d_une_autre_personne_est_refuse_sans_rien_consommer(google):
    """LE scénario du constat : A fabrique l'URL, B consent et termine dans SA session.
    Refus, rien d'échangé ni de rangé — et le state de A n'est pas brûlé pour autant."""
    etat = google_oauth.make_state(A, ORG)
    code, out = _terminer(etat, sub=B)
    assert (code, out["error"]) == (403, "state_not_yours")
    assert google["echanges"] == [] and google["ranges"] == []
    assert _terminer(etat, sub=A)[0] == 200


def test_un_state_expire_est_refuse(google, monkeypatch):
    vrai = time.time
    monkeypatch.setattr(oauth_flow.time, "time", lambda: vrai() - 601)
    etat = google_oauth.make_state(A, ORG)
    monkeypatch.setattr(oauth_flow.time, "time", vrai)
    code, out = _terminer(etat, sub=A)
    assert (code, out["error"]) == (400, "invalid_state")
    assert google["echanges"] == []


@pytest.mark.parametrize("abime", ["charge", "signature", "vide", "n'importe quoi"])
def test_un_state_altere_est_refuse(google, abime):
    corps, sig = google_oauth.make_state(A, ORG).split(".", 1)
    etat = {"charge": f"{corps[:-2]}xx.{sig}", "signature": f"{corps}.{sig[::-1]}",
            "vide": "", "n'importe quoi": abime}[abime]
    code, out = _terminer(etat, sub=A)
    assert (code, out["error"]) == (400, "invalid_state")
    assert google["echanges"] == []


def test_un_state_d_un_autre_flux_est_refuse(google):
    """Même secret, même forme : seule l'AUDIENCE distingue un state Microsoft d'un
    state Google. Rejoué ici, il est refusé."""
    etat = oauth_flow.sign_state("microsoft", {"sub": A, "org": ORG,
                                               "jti": oauth_flow.new_jti()})
    assert _terminer(etat, sub=A)[1]["error"] == "invalid_state"


def test_un_state_sans_jti_est_refuse(google):
    """Sans `jti`, l'usage unique n'est pas vérifiable : refus, pas d'exception."""
    etat = oauth_flow.sign_state("google", {"sub": A, "org": ORG, "c": "google"})
    assert _terminer(etat, sub=A)[1]["error"] == "invalid_state"


def test_un_echange_refuse_par_google_est_un_refus_nomme(google, monkeypatch):
    def _refus(code, sub):
        raise RuntimeError("invalid_grant")
    monkeypatch.setattr(google_oauth, "exchange_code", _refus)
    code, out = _terminer(google_oauth.make_state(A, ORG), sub=A)
    assert (code, out["error"]) == (502, "exchange_failed")
    assert "invalid_grant" not in (out.get("detail") or ""), "le diagnostic va au journal"
    assert google["ranges"] == []


# --- de bout en bout : callback réel, puis complétion -------------------------

def _callback():
    async def _options(_r):
        return None
    routes = datastore_routes.make_routes(
        None, None, lambda *a, **k: None, lambda *a, **k: None, lambda o: {}, _options)
    return next(r.endpoint for r in routes if r.path == "/api/google/oauth/callback")


def _retour_de_google(query: str) -> str:
    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    req = Request({"type": "http", "method": "GET", "path": "/api/google/oauth/callback",
                   "headers": [], "query_string": query.encode(), "path_params": {}},
                  receive=_receive)
    return asyncio.run(_callback()(req)).headers["location"]


@pytest.mark.usefixtures("connecteurs_tous_disponibles")
@pytest.mark.parametrize("qui_termine,attendu", [(A, 200), (B, 403)])
def test_de_bout_en_bout_seul_le_demandeur_range_le_compte(google, monkeypatch,
                                                           qui_termine, attendu):
    """Le flux complet : l'URL de consentement de A (vraie `build_auth_url`), le retour
    de Google sur le vrai callback, puis la complétion par la personne dont le front
    tient la session. Le callback n'a rien rangé ; seul A peut terminer."""
    monkeypatch.setattr(google_oauth, "_ctx_org", lambda sub: ORG)
    monkeypatch.setattr(google_oauth, "app_for", lambda sub: google_oauth.OAuthApp(
        client_id="cid", client_secret="sec", redirect_uri="https://oto.test/cb"))
    url = google_oauth.build_auth_url(A, connector="gmail")
    etat = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]

    retour = _retour_de_google(urllib.parse.urlencode({"code": "c/0de", "state": etat}))
    assert google["echanges"] == [] and google["ranges"] == [], "le callback ne range rien"
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(retour).query)
    assert q["connect"] == ["confirm"] and q["connector"] == ["gmail"]
    remis = urllib.parse.parse_qs(urllib.parse.urlsplit(retour).fragment)

    code, _out = _terminer(remis["state"][0], sub=qui_termine, code=remis["code"][0])
    assert code == attendu
    if attendu == 200:
        assert google["echanges"] == [("c/0de", A)]
        assert [r[0] for r in google["ranges"]] == [A]
    else:
        assert google["echanges"] == [] and google["ranges"] == []
