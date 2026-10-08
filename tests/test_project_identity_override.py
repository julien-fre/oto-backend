"""Surcharge d'identité connecteur par projet actif (ADR 0032 §4, B2.2).

`access.project_pinned_identity(connector)` lit la config PRÉFAITE du lien connecteur
du projet de l'APPEL (jeton project=, ADR 0038) → identity_id, ou None (repli défaut user). On
monkeypatche les seams (`current_call_project`, `list_project_links`), pas de DB.
"""
import pytest

from oto_mcp import access


@pytest.fixture
def wire(monkeypatch):
    state = {"pid": None, "links": []}
    monkeypatch.setattr(access.session_org, "current_call_project", lambda: state["pid"])
    monkeypatch.setattr(access.db, "list_project_links", lambda pid: state["links"])
    return state


def test_none_when_no_active_project(wire):
    wire["pid"] = None
    wire["links"] = [{"target_type": "connecteur", "target_ref": "google",
                      "config": {"identity_id": "a@x.co"}}]
    assert access.project_pinned_identity("google") is None


def test_returns_pinned_identity(wire):
    wire["pid"] = 7
    wire["links"] = [{"target_type": "connecteur", "target_ref": "google",
                      "identity_ref": "a@x.co", "config": {}}]
    assert access.project_pinned_identity("google") == "a@x.co"


def test_none_when_multiple_bindings_ambiguous(wire):
    # #57 : plusieurs bindings du même connecteur ⇒ ambigu ⇒ None (l'agent précise account=).
    wire["pid"] = 7
    wire["links"] = [{"target_type": "connecteur", "target_ref": "unipile", "identity_ref": "acc_A", "config": {}},
                     {"target_type": "connecteur", "target_ref": "unipile", "identity_ref": "acc_B", "config": {}}]
    assert access.project_pinned_identity("unipile") is None


def test_none_when_connector_not_pinned(wire):
    wire["pid"] = 7
    wire["links"] = [{"target_type": "connecteur", "target_ref": "unipile",
                      "config": {"identity_id": "acc_1"}}]
    assert access.project_pinned_identity("google") is None


def test_none_when_config_has_no_identity(wire):
    wire["pid"] = 7
    wire["links"] = [{"target_type": "connecteur", "target_ref": "google",
                      "config": {"instructions_md": "filtre thème mutuelle"}}]
    assert access.project_pinned_identity("google") is None


def test_explicit_project_id_overrides_session(wire):
    wire["pid"] = None  # pas de projet de session…
    wire["links"] = [{"target_type": "connecteur", "target_ref": "google",
                      "identity_ref": "b@x.co", "config": {}}]
    assert access.project_pinned_identity("google", project_id=7) == "b@x.co"


def test_fail_soft_on_error(wire, monkeypatch):
    wire["pid"] = 7
    monkeypatch.setattr(access.db, "list_project_links",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("db down")))
    assert access.project_pinned_identity("google") is None   # jamais d'exception


# ── Câblage : google_oauth.credentials_for honore le projet actif (incrément B) ──
# Depuis oto-backend#1160, le compte Google se choisit par la résolution commune
# (`access.resolve_credential`) : le pin y est lu sur le service appelé, puis sur le
# compte Google qui le porte. Ces trois bancs le jouent contre le vrai chemin, sur un
# faux coffre ; le reste (axe `_account=`, conflits, chaque service) :
# `tests/test_google_account_axis.py`.

def _deux_comptes(monkeypatch):
    from _coffre_google import installer

    env = installer(monkeypatch, org=39, sub="u1")
    env.coffre.poser("defaut@x.co", "RT-DEFAUT", defaut=True)
    env.coffre.poser("pinned@x.co", "RT-PIN")
    env.coffre.poser("explicit@x.co", "RT-EXPLICIT")
    return env


def test_credentials_for_applies_project_pin(monkeypatch):
    from oto_mcp.auth import google as google_oauth
    env = _deux_comptes(monkeypatch)
    env.epingles["google"] = "pinned@x.co"
    # account non passé → pin du projet (posé sur la carte du compte Google)
    assert google_oauth.credentials_for("u1", service="gmail").refresh_token == "RT-PIN"
    assert google_oauth.credentials_for("u1").refresh_token == "RT-PIN"


def test_credentials_for_explicit_account_wins(monkeypatch):
    from oto_mcp.auth import google as google_oauth
    env = _deux_comptes(monkeypatch)
    env.epingles["google"] = "pinned@x.co"
    # Un compte explicite passé par l'appelant prime sur le pin du projet.
    creds = google_oauth.credentials_for("u1", account="explicit@x.co", service="gmail")
    assert creds.refresh_token == "RT-EXPLICIT"


def test_credentials_for_no_project_keeps_default(monkeypatch):
    from oto_mcp.auth import google as google_oauth
    _deux_comptes(monkeypatch)                     # pas de projet/pin
    creds = google_oauth.credentials_for("u1", service="gmail")
    assert creds.refresh_token == "RT-DEFAUT"      # repli sur le défaut user (is_default)
