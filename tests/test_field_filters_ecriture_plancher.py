"""Écrire une politique de filtres de champs dit ce qu'elle expose du PLANCHER.

Signal oto #1269 : une org a posé `rules: []` sur `payfit` pour ouvrir les
documents, et a levé du même geste le NIR et l'IBAN de tous ses salariés dans toutes
les réponses — la réponse de l'écriture ne disait que `rules: 0`. Elle nomme
désormais, sous leur nom de sortie, les champs du défaut serveur que la politique
écrite laisse en clair (`unmasked`), avec une phrase (`warning`).
"""
import pytest

from oto_mcp import field_filter_defaults
from oto_mcp.capabilities.orgs import field_filters as ff


@pytest.fixture
def ecrit(monkeypatch):
    poses = {}
    monkeypatch.setattr(ff.org_store, "get_org", lambda org_id: {"id": org_id})
    monkeypatch.setattr(ff.org_store, "set_org_field_filters",
                        lambda org_id, service, block: poses.__setitem__(service, block))

    def _ecrire(service, rules):
        return ff._set_field_filter(None, ff.SetFieldFilterInput(
            org_id=1, service=service, rules=rules))

    _ecrire.poses = poses
    return _ecrire


PLANCHER = field_filter_defaults.champs_du_plancher("payfit")


def test_lifting_the_floor_names_every_field_now_in_clear(ecrit):
    out = ecrit("payfit", [])
    assert out["unmasked"] == PLANCHER
    assert "EN CLAIR" in out["warning"]
    assert "socialSecurityNumber" in out["warning"] and "iban" in out["warning"]


def test_a_policy_that_keeps_one_floor_field_masked_names_only_the_others(ecrit):
    out = ecrit("payfit", [{"fields": ["iban"], "action": "mask", "keep_last": 4}])
    assert "iban" not in out["unmasked"]
    assert "socialSecurityNumber" in out["unmasked"]
    assert out["warning"]


def test_clearing_the_policy_restores_the_floor_and_warns_of_nothing(ecrit):
    out = ecrit("payfit", None)
    assert out["cleared"] is True
    assert out["unmasked"] == [] and out.get("warning") is None


def test_a_service_without_a_floor_exposes_nothing_by_writing_nothing(ecrit):
    out = ecrit("pennylane", [])
    assert out["unmasked"] == [] and out.get("warning") is None


def test_the_floor_fields_are_read_from_the_server_default():
    """Les champs nommés sont ceux du défaut serveur, pas une liste recopiée."""
    attendus = [c for r in field_filter_defaults.SERVER_DEFAULTS["payfit"]["rules"]
                for c in r["fields"]]
    assert PLANCHER == attendus and "socialSecurityNumber" in PLANCHER
