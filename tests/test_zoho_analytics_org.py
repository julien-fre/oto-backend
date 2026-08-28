"""Analytics : déduire l'organisation quand c'est possible, la faire choisir sinon.

Zoho Analytics exige une organisation sur CHAQUE appel — le CRM la déduit du jeton,
Desk sait s'en passer en mono-org. Après un consentement, il restait donc à saisir un
identifiant à onze chiffres pris dans l'interface Zoho. L'API sait le dire ; mais elle
peut en rendre PLUSIEURS, sans en désigner aucune par défaut. Deviner serait faux une
fois sur deux (cas réel : deux organisations sur le premier compte testé).
"""
from __future__ import annotations

import pytest

from oto_mcp.auth import zoho as zoho_oauth


def _consent_fields():
    return {"client_id": "c", "client_secret": "s", "refresh_token": "rt",
            "data_center": "eu"}


def test_single_org_is_filled_in(monkeypatch):
    """Une seule réponse possible ⟹ l'utilisateur ne voit jamais la question."""
    monkeypatch.setattr(zoho_oauth, "analytics_orgs",
                        lambda f: [{"org_id": "20068608403", "name": "movinmotion",
                                    "role": "Organization Admin"}])
    assert zoho_oauth._derived_fields("zohoanalytics", _consent_fields()) == {
        "org_id": "20068608403"}


def test_several_orgs_are_never_guessed(monkeypatch):
    """Le cœur du sujet : prendre « la première » aurait désigné « Marvin » là où le
    compte travaille dans « movinmotion ». On laisse vide — l'état du credential
    signalera le champ manquant, et le choix se fera sur des noms."""
    monkeypatch.setattr(zoho_oauth, "analytics_orgs",
                        lambda f: [{"org_id": "20072252845", "name": "Marvin", "role": "Account Admin"},
                                   {"org_id": "20068608403", "name": "movinmotion", "role": "Organization Admin"}])
    assert zoho_oauth._derived_fields("zohoanalytics", _consent_fields()) == {}


def test_discovery_failure_never_loses_a_consent(monkeypatch):
    """Best-effort : au pire l'utilisateur retombe sur la saisie manuelle — jamais sur
    un consentement perdu parce qu'un appel annexe a échoué."""
    def boom(_fields):
        raise RuntimeError("zoho down")
    monkeypatch.setattr(zoho_oauth, "analytics_orgs", boom)
    assert zoho_oauth._derived_fields("zohoanalytics", _consent_fields()) == {}


def test_other_zoho_connectors_derive_nothing(monkeypatch):
    """CRM et Desk n'ont rien à déduire : aucun appel réseau ne doit partir."""
    monkeypatch.setattr(zoho_oauth, "analytics_orgs",
                        lambda f: pytest.fail("aucune découverte attendue"))
    assert zoho_oauth._derived_fields("zoho", _consent_fields()) == {}
    assert zoho_oauth._derived_fields("zohodesk", _consent_fields()) == {}


def test_org_id_stays_out_of_persisted_fields():
    """Tripwire : si `org_id` rejoignait les champs produits par le flux, un `org_id`
    NON déduit (cas à plusieurs organisations) cesserait d'être signalé comme manquant
    — le credential se dirait complet et échouerait au premier appel."""
    assert "org_id" not in zoho_oauth.PERSISTED_FIELDS


def test_unknown_region_is_rejected_before_any_call():
    with pytest.raises(zoho_oauth.ZohoOAuthError):
        zoho_oauth.analytics_orgs({"data_center": "xx"})
