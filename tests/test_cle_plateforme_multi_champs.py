"""Une clé plateforme d'un connecteur MULTI-CHAMPS se packe comme une clé d'org.

`POST /api/admin/platform-keys` stockait l'`api_key` reçue telle quelle. Pour `forager`
(api_key + account_id), ce blob n'était pas le JSON que lit `unpack_secret` : le relevé
de solde refusait « le blob multi-champs stocké n'est pas du JSON ». La console n'envoie
que `api_key` ; une `api_key` qui est déjà un objet JSON (contournement posé à la main)
est lue comme les champs, jamais ré-encodée.
"""
from __future__ import annotations

import json
import os

import pytest

from oto_mcp import credentials_store as cs


# --- La forme du blob (pur) -------------------------------------------------

def test_une_cle_brute_remplit_le_champ_secret_et_se_packe_en_json():
    blob = cs.platform_secret_from_input("forager", "  fg-key \n")
    assert json.loads(blob) == {"api_key": "fg-key"}
    assert cs.unpack_secret("forager", blob) == {"api_key": "fg-key"}


def test_une_cle_qui_est_deja_un_objet_json_n_est_pas_re_encodee():
    blob = cs.platform_secret_from_input(
        "forager", json.dumps({"api_key": " fg-key ", "account_id": "12"}))
    assert cs.unpack_secret("forager", blob) == {"api_key": "fg-key", "account_id": "12"}


def test_les_champs_par_nom_completent_la_cle():
    blob = cs.platform_secret_from_input("forager", "fg-key", {"account_id": " 12 "})
    assert cs.unpack_secret("forager", blob) == {"api_key": "fg-key", "account_id": "12"}
    seuls = cs.platform_secret_from_input("forager", "", {"api_key": "fg-key"})
    assert cs.unpack_secret("forager", seuls) == {"api_key": "fg-key"}


@pytest.mark.parametrize("api_key,fields,code", [
    ("   ", None, "missing_credentials"),
    ("", {"account_id": "12"}, "missing_credentials"),
    ('{"api_key": "  "}', None, "missing_credentials"),
    ("fg-key", {"region": "eu"}, "unknown_field"),
    ('{"api_key": "fg", "region": "eu"}', None, "unknown_field"),
    ("fg-key", {"api_key": "autre"}, "conflicting_api_key"),
])
def test_les_refus_nommes(api_key, fields, code):
    with pytest.raises(cs.CredentialFieldsInvalid) as e:
        cs.platform_secret_from_input("forager", api_key, fields)
    assert e.value.code == code


def test_un_connecteur_mono_cle_garde_la_valeur_brute():
    assert cs.platform_secret_from_input("serper", "  sk-1 ") == "sk-1"
    # Jamais relue comme du JSON : la valeur EST la clé.
    assert cs.platform_secret_from_input("serper", '{"a": 1}') == '{"a": 1}'
    with pytest.raises(cs.CredentialFieldsInvalid) as e:
        cs.platform_secret_from_input("serper", "  ")
    assert e.value.code == "empty_api_key"


def test_transcription_porte_ses_champs_non_secrets():
    blob = cs.platform_secret_from_input("transcription", "mk", {"language": "en"})
    assert cs.unpack_secret("transcription", blob) == {"api_key": "mk", "language": "en"}


# --- Bout en bout : la route pose, le coffre relit (PG jetable) ---------------

@pytest.fixture
def base(pg_dsn, monkeypatch):
    from _base_jetable import base_jetable

    monkeypatch.setenv("OTO_MCP_MASTER_KEY", os.urandom(32).hex())
    with base_jetable(pg_dsn):
        from oto_mcp.db import init_db
        init_db()
        yield


def _poser(monkeypatch, corps: dict):
    from _datastore_rest import call, stub_authz
    from oto_mcp.capabilities import _authz

    stub_authz(monkeypatch)
    monkeypatch.setattr(_authz.access, "is_super_admin", lambda sub: True)
    return call("platform.key.create", body=corps)


def _relue(provider: str, label: str) -> dict:
    ligne = cs.get_credential_with_meta(cs.PLATFORM, label, provider, "")
    return cs.unpack_secret(provider, ligne["secret"])


@pytest.mark.parametrize("api_key", ["fg-key", ' {"api_key": "fg-key"} '])
def test_forager_pose_par_la_route_se_relit_strictement_et_nait_fermee(
        base, monkeypatch, api_key):
    code, out = _poser(monkeypatch, {"provider": "forager", "label": "p",
                                     "api_key": api_key})
    assert (code, out) == (200, {"provider": "forager", "label": "p"})
    assert _relue("forager", "p") == {"api_key": "fg-key"}
    inst, = cs.list_platform_instances("forager")
    assert inst["share_mode"] == "closed"


def test_une_cle_vide_ou_un_champ_inconnu_n_ecrit_rien(base, monkeypatch):
    for corps, code_attendu in (
            ({"provider": "forager", "label": "p", "api_key": "   "}, "missing_fields"),
            ({"provider": "forager", "label": "p", "api_key": "k",
              "fields": {"region": "eu"}}, "unknown_field")):
        code, out = _poser(monkeypatch, corps)
        assert (code, out["error"]) == (400, code_attendu)
    assert cs.list_platform_instances("forager") == []


def test_serper_reste_une_cle_brute(base, monkeypatch):
    code, _ = _poser(monkeypatch, {"provider": "serper", "label": "free",
                                   "api_key": " sk-1 "})
    assert code == 200
    assert cs.get_credential(cs.PLATFORM, "free", "serper") == "sk-1"
