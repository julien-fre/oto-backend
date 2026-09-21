"""Le relais journalise, pour chaque échange de jeton, ce qu'on ne pouvait pas lire ailleurs :
la réponse de l'annuaire porte-t-elle un `refresh_token` (oui/non), et son `expires_in`.

Pourquoi. Caddy ne voit pas les corps, et l'échange de jeton d'un host de tenant relayé passe
par CE backend : la question « les clients OpenAI (Codex, ChatGPT) reçoivent-ils un refresh
token ? » n'avait pas de réponse lisible. La ligne existante (`grant`, `code`, `upstream`,
`error`, `ms`, `host`) gagne DEUX champs en fin de ligne, un booléen et un entier.

Ce banc tient trois choses :
- **les champs sont justes** : avec, sans `refresh_token`, en erreur, `expires_in` illisible ;
- **rien de secret n'est écrit** : ni la valeur d'un jeton, ni le sujet, ni le code
  d'autorisation, ni le `code_verifier`, ni la description d'une erreur de l'annuaire ;
- **le comportement ne change pas** : la réponse rendue au client est celle de l'annuaire, à
  l'octet près, et un grant hors contrat est refusé avant tout échange (donc sans ligne).
Ces gardes portent sur le chemin RELAYÉ seulement : sur un host non déclaré, le
`token_endpoint` annoncé est celui de Logto et ce backend ne voit pas l'échange.
"""
from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import tenancy
from oto_mcp.auth import facade, relay, relay_seals

_SECRET = b"secret-de-banc"
_R = "http://127.0.0.1:27890/callback"
_TENANT = "mcp.acme.test"
_BASE_TENANT = "https://mcp.acme.test"

# Des valeurs reconnaissables : si l'une apparaît dans un journal, le test le dit.
_AT, _RT, _IDT = "AT-SECRET-11aa", "RT-SECRET-22bb", "IDT-SECRET-33cc"
_SUB, _CODE, _VERIFIER = "SUB-SECRET-44dd", "CODE-SECRET-55ee", "V" * 20 + "-VERIF-66ff" + "V" * 20
_DESCRIPTION = "DESC-SECRET-77gg"
_INTERDITS = (_AT, _RT, _IDT, _SUB, _CODE, _VERIFIER, _DESCRIPTION)


class _Annuaire:
    def __init__(self):
        self.echanges = []
        self.reponse = httpx.Response(200, json={
            "access_token": _AT, "refresh_token": _RT, "id_token": _IDT, "sub": _SUB,
            "expires_in": 3600, "token_type": "Bearer"})

    async def poster(self, url, corps, headers):
        self.echanges.append((url, corps))
        return self.reponse


@pytest.fixture
def annuaire(monkeypatch):
    a = _Annuaire()
    monkeypatch.setattr(relay, "_poster", a.poster)
    relay._seaux.clear()
    yield a


@pytest.fixture
def client(monkeypatch, annuaire):
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.ninja")
    monkeypatch.setenv("LOGTO_PUBLIC_ENDPOINT", "https://auth.oto.cx")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", _SECRET.decode())
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_ID", "cid")
    monkeypatch.setenv("OTO_MCP_LOGTO_M2M_SECRET", "csec")
    monkeypatch.setenv("LOGTO_ACME_MGMT_ID", "cid")
    monkeypatch.setenv("LOGTO_ACME_MGMT_SECRET", "csec")
    monkeypatch.setenv("OTO_MCP_OAUTH_RELAY_HOSTS", _TENANT)
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc", tenants=[
            {"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc",
             "hosts": [_TENANT], "oauth_client_id": "app-acme",
             "logto_mgmt": {"token_endpoint": "https://admin.acme.test",
                            "api_endpoint": "https://auth.acme.test",
                            "credential": "LOGTO_ACME_MGMT"}}])))
    yield TestClient(Starlette(routes=facade.make_routes("https://mcp.oto.cx", "app-oto")))
    tenancy.install(avant)


def _echanger(client, corps):
    return client.post("/oauth/token", content=corps, headers={
        "host": _TENANT, "content-type": "application/x-www-form-urlencoded"})


_RAFRAICHIR = f"grant_type=refresh_token&refresh_token={_RT}&client_id=app-acme"


def _ligne(caplog) -> str:
    lignes = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("oauth.relay token ")]
    assert len(lignes) == 1, lignes
    return lignes[0]


def _sans_secret(caplog):
    for secret in _INTERDITS:
        assert secret not in caplog.text, f"{secret!r} a fuité dans un journal"


def test_avec_un_refresh_token_la_ligne_dit_oui_et_la_duree(client, annuaire, caplog):
    with caplog.at_level("INFO"):
        r = _echanger(client, _RAFRAICHIR)
    ligne = _ligne(caplog)
    assert r.status_code == 200
    for attendu in ("grant=refresh_token", "upstream=200", f"host={_TENANT}",
                    "refresh_token=oui", "expires_in=3600"):
        assert attendu in ligne, ligne
    _sans_secret(caplog)


def test_la_reponse_rendue_au_client_est_celle_de_lannuaire(client, annuaire, caplog):
    with caplog.at_level("INFO"):
        r = _echanger(client, _RAFRAICHIR)
    assert r.json() == {"access_token": _AT, "refresh_token": _RT, "id_token": _IDT,
                        "sub": _SUB, "expires_in": 3600, "token_type": "Bearer"}
    assert r.headers["cache-control"] == "no-store"


def test_sans_refresh_token_la_ligne_dit_non(client, annuaire, caplog):
    annuaire.reponse = httpx.Response(200, json={
        "access_token": _AT, "id_token": _IDT, "sub": _SUB, "expires_in": 3600})
    with caplog.at_level("INFO"):
        _echanger(client, _RAFRAICHIR)
    ligne = _ligne(caplog)
    assert "refresh_token=non" in ligne and "expires_in=3600" in ligne, ligne
    _sans_secret(caplog)


@pytest.mark.parametrize("valeur", [None, "", 0, False])
def test_un_refresh_token_vide_ou_nul_compte_pour_non(client, annuaire, caplog, valeur):
    annuaire.reponse = httpx.Response(200, json={"access_token": _AT, "refresh_token": valeur})
    with caplog.at_level("INFO"):
        _echanger(client, _RAFRAICHIR)
    assert "refresh_token=non" in _ligne(caplog)
    _sans_secret(caplog)


@pytest.mark.parametrize("ttl", ["3600", True, 3600.5, None, [3600]])
def test_un_expires_in_qui_nest_pas_un_entier_nest_pas_journalise(client, annuaire, caplog, ttl):
    annuaire.reponse = httpx.Response(200, json={"access_token": _AT, "expires_in": ttl})
    with caplog.at_level("INFO"):
        _echanger(client, _RAFRAICHIR)
    assert "expires_in=-" in _ligne(caplog)
    _sans_secret(caplog)


def test_une_erreur_de_lannuaire_journalise_le_code_pas_la_description(client, annuaire, caplog):
    annuaire.reponse = httpx.Response(400, json={
        "error": "invalid_grant", "error_description": f"le code {_DESCRIPTION} a expiré"})
    with caplog.at_level("INFO"):
        r = _echanger(client, _RAFRAICHIR)
    ligne = _ligne(caplog)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert "upstream=400" in ligne and "error='invalid_grant'" in ligne, ligne
    assert "refresh_token=-" in ligne and "expires_in=-" in ligne, ligne
    _sans_secret(caplog)


def test_un_succes_dont_le_json_est_illisible_journalise_un_point_dinterrogation(
        client, annuaire, caplog):
    annuaire.reponse = httpx.Response(200, content=b"{pas du json",
                                      headers={"content-type": "application/json"})
    with caplog.at_level("INFO"):
        _echanger(client, _RAFRAICHIR)
    assert "refresh_token=?" in _ligne(caplog)


def test_un_code_relaye_journalise_sans_le_code_ni_le_verifier(client, annuaire, caplog):
    marque = relay_seals.marquer_code(_SECRET, _BASE_TENANT, _R, _CODE)
    corps = (f"grant_type=authorization_code&code={marque}&redirect_uri={_R}"
             f"&code_verifier={_VERIFIER}&client_id=app-acme")
    with caplog.at_level("INFO"):
        r = _echanger(client, corps)
    ligne = _ligne(caplog)
    assert r.status_code == 200
    assert "grant=authorization_code" in ligne and "refresh_token=oui" in ligne, ligne
    assert marque not in caplog.text, "le code marqué du client a fuité dans un journal"
    _sans_secret(caplog)


def test_un_grant_hors_contrat_est_refuse_avant_tout_echange_donc_sans_ligne(
        client, annuaire, caplog):
    with caplog.at_level("INFO"):
        r = _echanger(client, "grant_type=client_credentials&client_id=app-acme")
    assert (r.status_code, r.json()["error"]) == (400, "unsupported_grant_type")
    assert annuaire.echanges == []
    assert not [x for x in caplog.records if x.getMessage().startswith("oauth.relay token ")]
