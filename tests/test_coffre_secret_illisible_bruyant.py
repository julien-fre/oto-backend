"""B6 et B7 de l'inventaire des silences (27/08) : le coffre ne rend pas `{}` en silence.

B6 — `credentials_store.unpack_secret` rendait `{}` quand le secret stocké ne se
relisait pas selon le format déclaré par le connecteur (base64 illisible, JSON
corrompu, JSON qui n'est pas un objet). Le client était alors **instancié sans
identifiants** et l'échec remontait comme une erreur d'authentification DU
FOURNISSEUR — on accusait la clé du client, jamais le coffre. Cas vécu : clé maître
périmée (`InvalidTag`) sur un credential `basic_auth`.

B7 — `zoho_oauth.app_fields` attrapait TOUTE exception de la résolution du credential
et retombait sur l'app d'éditeur oto. Une org qui a posé SON app pour la voir dans ses
logs Zoho basculait sur la nôtre au premier hoquet de coffre, sans un mot.
"""
from __future__ import annotations

import base64
import json

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp import credentials_store, zoho_oauth


# ── B6 : un secret illisible est une erreur de COFFRE, nommée ────────────────

def test_basic_auth_illisible_leve_au_lieu_de_rendre_vide():
    with pytest.raises(credentials_store.SecretUnpackError) as e:
        credentials_store.unpack_secret("planity", "\x00 pas du base64 \xff")
    assert "planity" in str(e.value)


def test_multi_champs_json_corrompu_leve():
    with pytest.raises(credentials_store.SecretUnpackError):
        credentials_store.unpack_secret("silae", '{"client_id": "abc"')


def test_multi_champs_json_qui_n_est_pas_un_objet_leve():
    # Un JSON VALIDE mais qui n'est pas un objet est tout aussi illisible : le code
    # d'avant rendait `{}` — donc un client sans identifiants — sans un mot.
    with pytest.raises(credentials_store.SecretUnpackError):
        credentials_store.unpack_secret("silae", '["client_id", "abc"]')


def test_le_chemin_nominal_ne_bouge_pas():
    packed = credentials_store.pack_secret("silae", {"client_id": "ci", "client_secret": "cs",
                                                     "subscription_key": "sk"})
    assert credentials_store.unpack_secret("silae", packed)["client_id"] == "ci"
    ba = base64.b64encode(b"a@b.c:motdepasse").decode()
    assert credentials_store.unpack_secret("planity", ba) == {"email": "a@b.c",
                                                              "password": "motdepasse"}


def test_un_client_ne_s_instancie_jamais_sans_identifiants(monkeypatch):
    """Le seam qui produisait le défaut : `ResolvedCredential.fields`, lu par tout
    connecteur multi-secrets pour se construire."""
    from oto_mcp.access.resolve import ResolvedCredential

    rc = ResolvedCredential(provider="silae", secret='{"client_id": "abc"',
                            is_platform=False, mode="org")
    with pytest.raises(credentials_store.SecretUnpackError):
        rc.fields


# ── B7 : une erreur de coffre ne bascule pas sur l'app d'éditeur ─────────────

@pytest.fixture
def editeur(monkeypatch):
    """oto publie une app d'éditeur pour la région — c'est vers elle que le silence
    faisait basculer."""
    monkeypatch.setattr(zoho_oauth.credentials_store, "get_editor_app",
                        lambda connector, dc: {"client_id": "APP-OTO", "client_secret": "s"})


def test_pas_encore_de_credential_reste_le_cas_nominal(editeur, monkeypatch):
    """L'état NOMINAL d'une première connexion : la cascade lève une McpError
    actionnable « aucun credential ». Le repli éditeur est LÉGITIME ici."""
    def _rien(*a, **k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message="Aucune clé zoho"))
    monkeypatch.setattr(zoho_oauth.access, "resolve_credential", _rien)
    assert zoho_oauth.app_fields("zoho", "u1", "eu")["client_id"] == "APP-OTO"


def test_erreur_de_coffre_refuse_au_lieu_de_basculer(editeur, monkeypatch):
    """Un déchiffrement qui échoue n'est PAS « pas encore de credential » : il ne
    doit jamais se lire comme l'absence d'app apportée."""
    from cryptography.exceptions import InvalidTag

    def _coffre_casse(*a, **k):
        raise InvalidTag()
    monkeypatch.setattr(zoho_oauth.access, "resolve_credential", _coffre_casse)
    with pytest.raises(InvalidTag):
        zoho_oauth.app_fields("zoho", "u1", "eu")
