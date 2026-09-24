"""La sonde de connexion Yousign — otomata-tech/oto#69. Couvre `auth` SEUL.

`list_signature_requests` est la lecture la moins chère sans effet de bord —
contrairement à `create_signature_request`, qui créerait un brouillon réel.
"""
from __future__ import annotations

import pytest

from oto_mcp import credentials_store
from oto_mcp.connectors import verify as cv
from oto_mcp.tools import yousign as Y


def _fields(secret: str, **autres) -> dict:
    """Champs EXACTEMENT comme la capacité verify les produit — coupler le test
    au vrai pack/unpack empêche le drift sonde↔schéma."""
    return credentials_store.unpack_secret(
        "yousign", credentials_store.pack_secret("yousign", {"key": secret, **autres}))


class _FauxClient:
    def __init__(self, rendu=None, leve=None):
        self._rendu = rendu if rendu is not None else []
        self._leve = leve
        self.appels = []

    def list_signature_requests(self):
        self.appels.append("list_signature_requests")
        if self._leve:
            raise self._leve
        return self._rendu


def _brancher(monkeypatch, client):
    import oto.tools.yousign as pkg

    def _construire(**kw):
        client.construit = kw
        return client

    monkeypatch.setattr(pkg, "YousignClient", _construire)
    return client


def test_un_credential_valide_ne_leve_pas(monkeypatch):
    cli = _brancher(monkeypatch, _FauxClient([{"id": "sr1"}]))
    Y._verify(_fields("k"))
    assert cli.appels == ["list_signature_requests"]


def test_401_leve_non_autorise(monkeypatch):
    from oto.tools.common import UpstreamHTTPError
    _brancher(monkeypatch, _FauxClient(
        leve=UpstreamHTTPError(401, {"message": "Unauthorized"}, service="yousign")))
    with pytest.raises(cv.NonAutorise):
        Y._verify(_fields("k"))


def test_une_erreur_non_auth_leve_sans_typer_non_autorise(monkeypatch):
    from oto.tools.common import UpstreamHTTPError
    _brancher(monkeypatch, _FauxClient(
        leve=UpstreamHTTPError(500, {"message": "server error"}, service="yousign")))
    with pytest.raises(RuntimeError) as e:
        Y._verify(_fields("k"))
    assert not isinstance(e.value, cv.SondeRefusee)


def test_la_sonde_vise_lhote_de_production_par_defaut(monkeypatch):
    cli = _brancher(monkeypatch, _FauxClient([]))
    Y._verify(_fields("k"))
    assert cli.construit == {"api_key": "k", "sandbox": False}


def test_la_sonde_dune_cle_sandbox_vise_lhote_sandbox(monkeypatch):
    """Une clé du bac à sable est refusée par l'hôte de production : sonder la
    production rendrait « non autorisé » pour une clé parfaitement valide."""
    cli = _brancher(monkeypatch, _FauxClient([]))
    Y._verify(_fields("k", environment="sandbox"))
    assert cli.construit == {"api_key": "k", "sandbox": True}
