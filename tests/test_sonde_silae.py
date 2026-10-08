"""La sonde de connexion Silae — otomata-tech/oto#69. Couvre `auth` SEUL.

`POST v1/InfosTechniquesDossiers/ListeDossiers` avec `{"typeDossiers": 0}` (le corps vide
`{}` vaut l'erreur 1001). Le client LÈVE désormais (`UpstreamHTTPError`, `SilaeAuthError`
en 401) : la sonde distingue un REFUS d'accès (401/403 → `NonAutorise`) d'une PANNE
(5xx, réseau, 400 de notre fait → levée telle quelle, verdict `unknown`). Credential à 3
champs (ADR 0011), round-trip par le VRAI `pack_secret`/`unpack_secret`.
"""
from __future__ import annotations

import pytest
import requests

from oto.tools.common.errors import UpstreamHTTPError
from oto_mcp import credentials_store
from oto_mcp.connectors import verify as cv
from oto_mcp.tools import silae as S


def _fields(client_id: str, client_secret: str, subscription_key: str) -> dict:
    secret = credentials_store.pack_secret("silae", {
        "client_id": client_id, "client_secret": client_secret,
        "subscription_key": subscription_key,
    })
    return credentials_store.unpack_secret("silae", secret)


class _FauxClient:
    def __init__(self, rendu=None, leve=None):
        self._rendu = rendu
        self._leve = leve
        self.appels = []

    def list_dossiers(self):
        self.appels.append("list_dossiers")
        if self._leve is not None:
            raise self._leve
        return self._rendu


def _brancher(monkeypatch, client):
    import oto.tools.silae as pkg
    monkeypatch.setattr(pkg, "SilaeClient", lambda **kw: client)
    return client


def test_un_credential_valide_ne_leve_pas_liste_vide(monkeypatch):
    cli = _brancher(monkeypatch, _FauxClient({"listeDossiers": []}))
    S._verify(_fields("id", "sec", "sub"))
    assert cli.appels == ["list_dossiers"]


@pytest.mark.parametrize("code", [401, 403])
def test_un_refus_d_acces_leve_non_autorise(monkeypatch, code):
    """Clé d'abonnement invalide, jeton refusé, configuration d'accès trop étroite."""
    _brancher(monkeypatch, _FauxClient(leve=UpstreamHTTPError(
        code, {"errors": [{"code": str(code), "message": "SubscriptionKeyInvalid"}]},
        service="silae")))
    with pytest.raises(cv.NonAutorise) as e:
        S._verify(_fields("id", "sec", "sub"))
    assert cv.classer(e.value) == cv.UNAUTHORIZED


def test_un_identifiant_refuse_au_jeton_leve_non_autorise(monkeypatch):
    from oto.tools.silae import SilaeAuthError

    _brancher(monkeypatch, _FauxClient(leve=SilaeAuthError("invalid_client")))
    with pytest.raises(cv.NonAutorise):
        S._verify(_fields("id", "sec", "sub"))


def test_une_panne_amont_n_est_pas_un_refus(monkeypatch):
    _brancher(monkeypatch, _FauxClient(leve=UpstreamHTTPError(
        500, {"errors": [{"code": "500", "message": "server error"}]}, service="silae")))
    with pytest.raises(UpstreamHTTPError) as e:
        S._verify(_fields("id", "sec", "sub"))
    assert not isinstance(e.value, cv.SondeRefusee)
    assert cv.classer(e.value) == cv.UNKNOWN


def test_une_panne_reseau_n_est_pas_un_refus(monkeypatch):
    _brancher(monkeypatch, _FauxClient(leve=requests.ConnectionError("dns")))
    with pytest.raises(requests.ConnectionError) as e:
        S._verify(_fields("id", "sec", "sub"))
    assert cv.classer(e.value) == cv.UNKNOWN


def test_la_sonde_est_enregistree_avec_la_couverture_auth():
    from fastmcp import FastMCP

    S.register(FastMCP("t"))
    assert cv.supports("silae")
    assert cv.probe_for("silae") is S._verify
    assert cv.couverture("silae") == cv.AUTH
