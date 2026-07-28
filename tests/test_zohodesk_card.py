"""Carte `zohodesk` : `org_id` FACULTATIF, région exigée.

Un credential scopé `Desk.articles.READ` seul ne peut pas découvrir son orgId
(`/organizations` → 403 SCOPE_MISMATCH) — et n'en a pas besoin : les endpoints KB
résolvent le portail depuis le token mono-org. Tant que le champ était requis, ce
credential était **impossible à poser**. La région, elle, reste obligatoire : un
self-client est lié à son data center, et un mauvais domaine renvoie un
`invalid_client` opaque.
"""
from __future__ import annotations

import pytest

from oto_mcp import credentials_store as cs
from oto_mcp import providers

BASE = {"client_id": "1000.X", "client_secret": "s3cr3t",
        "refresh_token": "1000.r", "data_center": "eu"}


def _fields():
    return {f.name: f for f in providers.REGISTRY["zohodesk"].secret_fields}


def test_org_id_is_optional():
    assert _fields()["org_id"].required is False


@pytest.mark.parametrize("name", ["client_id", "client_secret", "refresh_token",
                                  "data_center"])
def test_other_fields_stay_required(name):
    assert _fields()[name].required is True


def test_can_post_without_org_id():
    """Le cas réel : scope articles-only, aucun orgId disponible."""
    packed = cs.secret_from_input("zohodesk", None, dict(BASE))
    assert sorted(cs.unpack_secret("zohodesk", packed)) == [
        "client_id", "client_secret", "data_center", "refresh_token"]


def test_org_id_still_stored_when_given():
    """Facultatif ≠ ignoré : fourni, il doit être conservé (endpoints tickets)."""
    packed = cs.secret_from_input("zohodesk", None, {**BASE, "org_id": "800123456"})
    assert cs.unpack_secret("zohodesk", packed)["org_id"] == "800123456"


@pytest.mark.parametrize("missing", ["client_secret", "refresh_token", "data_center"])
def test_missing_required_field_is_refused(missing):
    payload = {k: v for k, v in BASE.items() if k != missing}
    with pytest.raises(ValueError, match="missing_credentials"):
        cs.secret_from_input("zohodesk", None, payload)


def test_data_center_is_a_config_field_not_a_secret():
    """Région et orgId voyagent avec la clé comme CONFIG (non chiffrée à l'affichage)."""
    cfg = {f.name for f in providers.REGISTRY["zohodesk"].config_fields}
    assert {"org_id", "data_center"} <= cfg
