"""Écrire une politique de filtres de champs sur un service à PLANCHER.

Signal oto #1269 : une org a posé `rules: []` sur `payfit` pour ouvrir les
documents, et a levé du même geste le NIR et l'IBAN de tous ses salariés dans toutes
les réponses — la réponse de l'écriture ne disait que `rules: 0`. Décision du
06/10/2026 :
- un champ du plancher ne sort en clair que NOMMÉ (`unmask`) ;
- les documents s'ouvrent à part (`documents: true`), sans lever aucun champ ;
- `rules: []` seul sur un service à plancher est refusé en montrant la forme
  explicite ;
- la réponse nomme les champs du plancher désormais en clair (`unmasked`), avec une
  phrase (`warning`).
"""
import pytest

from oto_mcp import field_filter_defaults
from oto_mcp.capabilities._types import AuthzDenied
from oto_mcp.capabilities.orgs import field_filters as ff


@pytest.fixture
def ecrit(monkeypatch):
    poses = {}
    monkeypatch.setattr(ff.org_store, "get_org", lambda org_id: {"id": org_id})
    monkeypatch.setattr(ff.org_store, "set_org_field_filters",
                        lambda org_id, service, block: poses.__setitem__(service, block))

    def _ecrire(service, rules, **extra):
        return ff._set_field_filter(None, ff.SetFieldFilterInput(
            org_id=1, service=service, rules=rules, **extra))

    _ecrire.poses = poses
    return _ecrire


PLANCHER = field_filter_defaults.champs_du_plancher("payfit")


def _refus(fn, code):
    with pytest.raises(AuthzDenied) as exc:
        fn()
    assert exc.value.code == code
    return str(exc.value)


def test_an_empty_policy_on_a_floor_is_refused_and_shows_the_explicit_form(ecrit):
    message = _refus(lambda: ecrit("payfit", []), "floor_lift_must_be_explicit")
    assert "unmask" in message and "documents: true" in message and "rules: null" in message
    assert "socialSecurityNumber" in message
    assert "payfit" not in ecrit.poses        # rien n'a été écrit


def test_naming_every_floor_field_lifts_them_and_says_so(ecrit):
    out = ecrit("payfit", [], unmask=PLANCHER)
    assert out["unmasked"] == PLANCHER
    assert "EN CLAIR" in out["warning"]
    assert ecrit.poses["payfit"] == {"rules": [], "unmask": PLANCHER}


def test_naming_one_field_lifts_only_that_one(ecrit):
    out = ecrit("payfit", [], unmask=["iban"])
    assert out["unmasked"] == ["iban"]


def test_consenting_to_documents_unmasks_nothing(ecrit):
    out = ecrit("payfit", [], documents=True)
    assert out["unmasked"] == [] and out.get("warning") is None
    assert ecrit.poses["payfit"] == {"rules": [], "documents": True}


def test_an_org_rule_adds_to_the_floor(ecrit):
    out = ecrit("payfit", [{"fields": ["birthDate"], "action": "drop"}])
    assert out["unmasked"] == []


def test_an_org_rule_on_a_floor_field_replaces_its_floor_rule(ecrit):
    """Viser un champ du plancher, c'est le nommer : la règle de l'org s'applique à sa
    place (ici un masque qui garde 4 caractères, toujours un masque)."""
    out = ecrit("payfit", [{"fields": ["iban"], "action": "mask", "keep_last": 4}])
    assert "iban" not in out["unmasked"]


@pytest.mark.parametrize("unmask", [["birthDate"], []])
def test_unmask_only_names_floor_fields(ecrit, unmask):
    _refus(lambda: ecrit("payfit", [], unmask=unmask), "unknown_floor_field")


def test_unmask_on_a_service_without_a_floor_is_refused(ecrit):
    _refus(lambda: ecrit("pennylane", [], unmask=["iban"]), "no_floor")


def test_documents_on_a_service_without_documents_is_refused(ecrit):
    _refus(lambda: ecrit("pennylane", [], documents=True), "no_documents")


def test_unmask_or_documents_need_a_policy(ecrit):
    _refus(lambda: ecrit("payfit", None, documents=True), "rules_required")


def test_clearing_the_policy_restores_the_floor_and_warns_of_nothing(ecrit):
    out = ecrit("payfit", None)
    assert out["cleared"] is True
    assert out["unmasked"] == [] and out.get("warning") is None


def test_a_service_without_a_floor_keeps_its_authoritative_empty_policy(ecrit):
    """Sans plancher, `rules: []` garde son sens : rien n'est masqué, rien n'est
    refusé (le cas d'une politique interne existante sur un connecteur de paie)."""
    out = ecrit("silae", [])
    assert out["unmasked"] == [] and ecrit.poses["silae"] == {"rules": []}
    assert "silae" not in field_filter_defaults.SERVER_DEFAULTS


def test_the_floor_fields_are_read_from_the_server_default():
    """Les champs nommés sont ceux du défaut serveur, pas une liste recopiée."""
    attendus = [c for r in field_filter_defaults.SERVER_DEFAULTS["payfit"]["rules"]
                for c in r["fields"]]
    assert PLANCHER == attendus and "socialSecurityNumber" in PLANCHER
