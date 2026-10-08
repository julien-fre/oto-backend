"""Un connecteur COUPÉ (`connector_availability`, tout palier) ne se connecte pas.

La garde d'appel (`activation_gate`, refus `connector_disabled`) refusait de SERVIR un
connecteur coupé, mais son flux de connexion ouvrait encore un consentement : sur une
instance dont l'app OAuth est vérifiée pour une liste fermée de services, ce consentement
demandait des scopes hors liste. Chaque point d'entrée qui démarre une connexion refuse
désormais AVANT de construire une URL ou d'écrire quoi que ce soit, avec le même code
et la même lecture de la coupure que la garde d'appel.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from oto_mcp.capabilities._types import AuthzDenied
from oto_mcp.connectors import activation, activation_gate
from oto_mcp.connectors import flow as connector_flow


@pytest.fixture
def coupe(monkeypatch):
    """Coupe les connecteurs listés au palier plateforme, pour l'org 42."""
    coupes: set[str] = set()
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: 42)
    monkeypatch.setattr("oto_mcp.access.current_group", lambda sub: None)
    monkeypatch.setattr(activation, "cran_qui_coupe",
                        lambda con, org=None, group=None:
                        "platform" if con in coupes else None)
    return coupes


def _ctx():
    return SimpleNamespace(sub="sub-1", org_id=42, group_id=None)


def _interdit(*a, **k):
    raise AssertionError("rien ne doit être construit pour un connecteur coupé")


# --- le chemin commun : `connector_flow.start` ---------------------------------

@pytest.fixture
def flux_temoin():
    appels = []

    def start(ctx, values):
        appels.append(values)
        return connector_flow.FlowStart(auth_url="https://consent.example/x")

    connector_flow.declare("temoin_coupe", start=start)
    yield appels
    connector_flow._FLOWS.pop("temoin_coupe", None)


def test_le_flux_commun_refuse_un_connecteur_coupe_avant_de_le_demarrer(coupe, flux_temoin):
    coupe.add("temoin_coupe")
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(connector_flow.start("temoin_coupe", _ctx(), {}))
    assert (e.value.status, e.value.code) == (403, "connector_disabled")
    assert "temoin_coupe" in e.value.message and "no connection" in e.value.message
    assert e.value.details["scope"] == "platform"
    assert flux_temoin == []


def test_le_flux_commun_demarre_un_connecteur_disponible(coupe, flux_temoin):
    out = asyncio.run(connector_flow.start("temoin_coupe", _ctx(), {"a": 1}))
    assert out.auth_url == "https://consent.example/x"
    assert flux_temoin == [{"a": 1}]


# --- Google : le compte et chaque service, y compris la route historique --------

@pytest.mark.parametrize("service", ["tasks", "chat", "bigquery", "google"])
def test_google_ne_construit_aucune_url_pour_un_service_coupe(coupe, monkeypatch, service):
    from oto_mcp.auth import google

    coupe.add(service)
    monkeypatch.setattr(google, "_ctx_org", _interdit)
    with pytest.raises(AuthzDenied) as e:
        google.build_auth_url("sub-1", connector=service)
    assert e.value.code == "connector_disabled"


def test_la_route_historique_de_google_refuse_aussi(coupe, monkeypatch):
    from oto_mcp.auth import google
    from oto_mcp.capabilities import federated_oauth

    coupe.add("google")
    monkeypatch.setattr(google, "_ctx_org", _interdit)
    with pytest.raises(AuthzDenied) as e:
        federated_oauth._google_start(_ctx(), federated_oauth.OAuthStartInput())
    assert e.value.code == "connector_disabled"


# --- Zoho : la face MCP vise un connecteur que son espace de noms ne dit pas ------

def test_zoho_refuse_le_connecteur_cible_coupe(coupe, monkeypatch):
    from oto_mcp.capabilities import zoho_connect

    coupe.add("zohodesk")
    monkeypatch.setattr(zoho_connect.zoho_oauth, "build_auth_url", _interdit)
    with pytest.raises(AuthzDenied) as e:
        zoho_connect.start_for(_ctx(), "zohodesk", "eu")
    assert e.value.code == "connector_disabled"


# --- messagerie hébergée : le canal, pas la clé `unipile` -------------------------

def test_un_canal_heberge_coupe_n_ouvre_aucun_assistant(coupe, monkeypatch):
    from oto_mcp import providers, unipile_connect

    canal = providers.connector_for_hosted_channel("WHATSAPP")
    assert canal is not None
    coupe.add(canal.name)
    monkeypatch.setattr("oto_mcp.access.resolve_credential", _interdit)
    with pytest.raises(unipile_connect.ConnectRefused) as e:
        asyncio.run(unipile_connect.hosted_auth_url("sub-1", "whatsapp"))
    assert (e.value.status, e.value.code) == (403, "connector_disabled")


# --- session navigateur (Live View) ---------------------------------------------

def test_une_session_navigateur_n_est_pas_ouverte_pour_un_connecteur_coupe(coupe, monkeypatch):
    from oto_mcp import browser_session
    from oto_mcp.capabilities import browser_sessions

    nom = "temoin_session"
    coupe.add(nom)
    monkeypatch.setattr(browser_session, "is_session_connector", lambda c: c == nom)
    monkeypatch.setattr(browser_session, "start", _interdit)
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(browser_sessions._start(
            _ctx(), browser_sessions.SessionStartInput(name=nom)))
    assert e.value.code == "connector_disabled"


# --- une seule lecture de la coupure ---------------------------------------------

def test_la_garde_d_appel_et_la_garde_de_connexion_lisent_la_meme_coupure(coupe, monkeypatch):
    """Le palier et le geste nommés sont les mêmes : `_coupure` est partagée."""
    coupe.add("temoin_coupe")
    monkeypatch.setattr("oto_mcp.call_axes.current_user_sub_from_token", lambda: "sub-1")
    monkeypatch.setattr("oto_mcp.org_suspension.refus", lambda org: None)
    appel = activation_gate._refus("temoin_coupe")
    with pytest.raises(activation_gate.ConnecteurCoupe) as e:
        activation_gate.exiger_connectable("temoin_coupe", "sub-1")
    assert appel.data["scope"] == e.value.details["scope"] == "platform"
    assert appel.data["code"] == e.value.code == "connector_disabled"


def test_sans_sub_rien_n_est_garde(coupe):
    coupe.add("temoin_coupe")
    activation_gate.exiger_connectable("temoin_coupe", None)
