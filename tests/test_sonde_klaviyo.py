"""La sonde de connexion Klaviyo. Couvre la clé et le scope `accounts:read`.

`GET /accounts` : la lecture la moins chère, sans effet de bord — c'est aussi la
sonde que déclare la description du connecteur dans la bibliothèque.
"""
from __future__ import annotations

import pytest

from oto_mcp import credentials_store
from oto_mcp.connectors import verify as cv
from oto_mcp.tools import klaviyo as K
from oto_mcp.tools import klaviyo_socle as S


def _fields(secret: str) -> dict:
    """Champs EXACTEMENT comme la capacité verify les produit — coupler le test
    au vrai pack/unpack empêche le drift sonde↔schéma."""
    return credentials_store.unpack_secret(
        "klaviyo", credentials_store.pack_secret("klaviyo", {"key": secret}))


class _FauxClient:
    def __init__(self, leve=None):
        self._leve = leve
        self.appels = 0

    def get_account(self):
        self.appels += 1
        if self._leve:
            raise self._leve
        return {"data": [{"type": "account", "id": "Ab1"}]}


def _brancher(monkeypatch, client):
    import oto.tools.klaviyo as pkg

    def _construire(**kw):
        client.construit = kw
        return client

    monkeypatch.setattr(pkg, "KlaviyoClient", _construire)
    return client


def test_la_sonde_est_enregistree():
    from fastmcp import FastMCP
    K.register(FastMCP("t"))
    assert cv.supports("klaviyo")


def test_une_cle_valide_ne_leve_pas(monkeypatch):
    cli = _brancher(monkeypatch, _FauxClient())
    S._verify(_fields("pk_k"))
    assert cli.appels == 1
    assert cli.construit == {"api_key": "pk_k"}


@pytest.mark.parametrize("status,code", [(401, "key_invalid"), (403, "scope_missing")])
def test_un_refus_dauthentification_se_classe_non_autorise(monkeypatch, status, code):
    from oto.tools.klaviyo import KlaviyoError
    _brancher(monkeypatch, _FauxClient(leve=KlaviyoError(status, code, "no")))
    with pytest.raises(KlaviyoError) as e:
        S._verify(_fields("pk_k"))
    assert cv.classer(e.value) == cv.UNAUTHORIZED


def test_une_erreur_serveur_ne_se_classe_pas_non_autorise(monkeypatch):
    from oto.tools.klaviyo import KlaviyoError
    _brancher(monkeypatch, _FauxClient(
        leve=KlaviyoError(503, "upstream_unavailable", "busy", retryable=True)))
    with pytest.raises(KlaviyoError) as e:
        S._verify(_fields("pk_k"))
    assert cv.classer(e.value) == cv.UNKNOWN
