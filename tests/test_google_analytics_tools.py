"""Les tools `ga4_*` (Google Analytics 4, lecture par compte de service).

Le VRAI client oto-core sur une doublure de transport : la `requests.Session` que
`GA4Client` construit est remplacée par une session dont l'adaptateur sert les
réponses (jeton compris). Ce qui est verrouillé ici : la surface (cinq tools), le
credential à un champ, la vue en table d'un rapport, la projection du catalogue,
les deux refus nommés (nom invalide → `ga4_metadata` ; accès manquant → l'email du
compte de service), et la sonde (identité + propriétés visibles, zéro = refus).

Aucun identifiant réel : propriété, compte et email sont factices, la clé RSA est
générée à la volée.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

import pytest
import requests
from requests.adapters import BaseAdapter

from oto_mcp.mcp_errors import McpError

pytestmark = pytest.mark.exige_pin_oto_core

EMAIL = "lecteur@projet-factice.iam.gserviceaccount.com"
PROP = "properties/123456789"


@pytest.fixture(scope="module")
def key_json() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "private_key_id": "kid-factice",
                       "private_key": pem, "client_email": EMAIL,
                       "token_uri": "https://oauth2.googleapis.com/token"})


class _Transport(BaseAdapter):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.routes = {("POST", "/token"): (200, {"access_token": "jeton", "expires_in": 3599})}

    def on(self, method, path, status, body):
        self.routes[(method, path)] = (status, body)

    def send(self, request, **kwargs):
        self.sent.append(request)
        u = urlparse(request.url)
        path = "/token" if u.netloc == "oauth2.googleapis.com" else u.path
        if (request.method, path) not in self.routes:
            raise AssertionError(f"requête inattendue : {request.method} {request.url}")
        status, body = self.routes[(request.method, path)]
        resp = requests.Response()
        resp.status_code, resp._content = status, json.dumps(body).encode()
        resp.url, resp.request = request.url, request
        return resp

    def close(self):
        pass


@pytest.fixture
def transport(monkeypatch, key_json):
    from oto.tools.google_analytics import auth as ga_auth
    from oto.tools.google_analytics import client as ga_client

    t = _Transport()

    class _Session(requests.Session):
        def __init__(self):
            super().__init__()
            self.mount("https://", t)

    monkeypatch.setattr(ga_client.requests, "Session", _Session)
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider: {"service_account_json": key_json})
    ga_auth._TOKEN_CACHE.clear()
    yield t
    ga_auth._TOKEN_CACHE.clear()


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import google_analytics as G

    m = FastMCP("t")
    G.register(m)
    return m


def _tool(name):
    return asyncio.run(_mcp().get_tool(name)).fn


def _body(t, suffix):
    return json.loads(next(r for r in t.sent if r.url.endswith(suffix)).body)


_SUMMARIES = {"accountSummaries": [{
    "name": "accountSummaries/1", "account": "accounts/1", "displayName": "Compte",
    "propertySummaries": [{"property": PROP, "displayName": "Site", "parent": "accounts/1",
                           "propertyType": "PROPERTY_TYPE_ORDINARY"}]}]}

_REPORT = {
    "dimensionHeaders": [{"name": "eventName"}],
    "metricHeaders": [{"name": "eventCount", "type": "TYPE_INTEGER"}],
    "rows": [{"dimensionValues": [{"value": "page_view"}], "metricValues": [{"value": "42"}]},
             {"dimensionValues": [{"value": "achat"}], "metricValues": [{"value": "3"}]}],
    "rowCount": 5, "metadata": {"currencyCode": "EUR", "subjectToThresholding": True},
    "kind": "analyticsData#runReport",
}


def test_la_surface_est_exactement_les_cinq_tools(transport):
    assert sorted(t.name for t in asyncio.run(_mcp().list_tools())) == [
        "ga4_key_events", "ga4_metadata", "ga4_properties", "ga4_realtime", "ga4_report"]


def test_le_credential_est_un_champ_secret_unique_aux_blancs_significatifs():
    """Le PEM de la clé contient des espaces (« BEGIN PRIVATE KEY ») : le nettoyage
    par défaut, qui retire tous les blancs, la rendrait illisible."""
    from oto_mcp import providers

    c = providers.REGISTRY["google_analytics"]
    (champ,) = c.secret_fields
    assert (champ.name, champ.secret, champ.whitespace_significant) == (
        "service_account_json", True, True)
    assert c.secret_kind == "fields" and c.org_shareable and c.namespaces == ("ga4",)


def test_proprietes_vue_compacte(transport):
    transport.on("GET", "/v1beta/accountSummaries", 200, _SUMMARIES)
    out = _tool("ga4_properties")()
    assert out == {"accounts": [{"account": "accounts/1", "name": "Compte", "properties": [
        {"property": PROP, "name": "Site"}]}], "property_count": 1}


def test_proprietes_avec_flux(transport):
    transport.on("GET", "/v1beta/accountSummaries", 200, _SUMMARIES)
    transport.on("GET", f"/v1beta/{PROP}/dataStreams", 200, {"dataStreams": [{
        "name": f"{PROP}/dataStreams/9", "type": "WEB_DATA_STREAM", "displayName": "Web",
        "webStreamData": {"measurementId": "G-FACTICE", "defaultUri": "https://exemple.fr"}}]})
    out = _tool("ga4_properties")(include_streams=True)
    assert out["accounts"][0]["properties"][0]["streams"] == [{
        "stream": f"{PROP}/dataStreams/9", "type": "WEB_DATA_STREAM", "name": "Web",
        "measurement_id": "G-FACTICE", "url": "https://exemple.fr"}]


def test_rapport_par_defaut_30_jours_en_table(transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 200, _REPORT)
    out = _tool("ga4_report")(property="123456789", metrics=["eventCount"],
                              dimensions=["eventName"], limit=2)
    body = _body(transport, ":runReport")
    assert body["dateRanges"] == [{"startDate": "30daysAgo", "endDate": "yesterday"}]
    assert body["limit"] == 2 and "offset" not in body
    assert out["columns"] == ["eventName", "eventCount"]
    assert out["rows"] == [["page_view", 42], ["achat", 3]]
    assert out["row_count"] == 5 and out["next_offset"] == 2
    assert out["metadata"]["subjectToThresholding"] is True


def test_rapport_full_rend_le_brut(transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 200, _REPORT)
    assert _tool("ga4_report")(property=PROP, metrics=["eventCount"], full=True) == _REPORT


def test_nom_invalide_refus_nomme_vers_ga4_metadata(transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 400, {"error": {
        "code": 400, "status": "INVALID_ARGUMENT",
        "message": "Field fauxNom is not a valid metric."}})
    with pytest.raises(McpError) as ei:
        _tool("ga4_report")(property=PROP, metrics=["fauxNom"])
    msg = str(ei.value)
    assert "fauxNom" in msg and "ga4_metadata" in msg


def test_acces_manquant_refus_nomme_l_email_du_compte_de_service(transport):
    transport.on("POST", f"/v1beta/{PROP}:runReport", 403, {"error": {
        "code": 403, "status": "PERMISSION_DENIED",
        "message": "User does not have sufficient permissions for this property."}})
    with pytest.raises(McpError) as ei:
        _tool("ga4_report")(property=PROP, metrics=["sessions"])
    msg = str(ei.value)
    assert EMAIL in msg and "Lecteur" in msg and PROP in msg


def test_identifiant_de_mesure_refuse_avant_tout_appel(transport):
    with pytest.raises(McpError, match="Propriété GA4 invalide"):
        _tool("ga4_report")(property="G-FACTICE", metrics=["sessions"])
    assert not [r for r in transport.sent if "analyticsdata" in r.url]


def test_temps_reel_active_users_par_defaut(transport):
    transport.on("POST", f"/v1beta/{PROP}:runRealtimeReport", 200, {
        "dimensionHeaders": [], "metricHeaders": [{"name": "activeUsers",
                                                   "type": "TYPE_INTEGER"}]})
    out = _tool("ga4_realtime")(property=PROP)
    assert _body(transport, ":runRealtimeReport")["metrics"] == [{"name": "activeUsers"}]
    assert out["rows"] == [] and out["columns"] == ["activeUsers"]


_META = {"name": f"{PROP}/metadata",
         "dimensions": [{"apiName": "country", "uiName": "Pays", "category": "Géo",
                         "description": "x" * 200},
                        {"apiName": "customEvent:plan", "uiName": "Plan",
                         "category": "Custom", "customDefinition": True}],
         "metrics": [{"apiName": "totalRevenue", "uiName": "Revenu", "category": "Commerce",
                      "type": "TYPE_CURRENCY", "description": "revenue total"}]}


def test_catalogue_par_defaut_groupe_par_categorie_sans_descriptions(transport):
    transport.on("GET", f"/v1beta/{PROP}/metadata", 200, _META)
    out = _tool("ga4_metadata")(property=PROP)
    assert out["dimensions"] == {"Géo": ["country"], "Custom": ["customEvent:plan"]}
    assert out["metrics"] == {"Commerce": ["totalRevenue"]}
    assert out["counts"] == {"dimensions": 2, "metrics": 1}
    assert "x" * 200 not in json.dumps(out)


def test_catalogue_search_rend_le_detail(transport):
    transport.on("GET", f"/v1beta/{PROP}/metadata", 200, _META)
    out = _tool("ga4_metadata")(property=PROP, kind="metrics", search="REVENUE")
    assert out == {"metrics": [{"api_name": "totalRevenue", "ui_name": "Revenu",
                                "category": "Commerce", "type": "TYPE_CURRENCY",
                                "description": "revenue total"}]}


def test_evenements_cles(transport):
    transport.on("GET", f"/v1beta/{PROP}/keyEvents", 200, {"keyEvents": [{
        "name": f"{PROP}/keyEvents/1", "eventName": "achat",
        "countingMethod": "ONCE_PER_EVENT", "createTime": "2026-01-01T00:00:00Z"}]})
    out = _tool("ga4_key_events")(property=PROP)
    assert out == {"key_events": [{"event_name": "achat", "counting_method": "ONCE_PER_EVENT",
                                   "created": "2026-01-01T00:00:00Z"}], "count": 1}


# --- la sonde -------------------------------------------------------------------

def _verify():
    from oto_mcp.connectors import verify as V

    _mcp()
    return V.probe_for("google_analytics")


def test_sonde_rend_l_identite_et_le_nombre_de_proprietes(transport, key_json):
    transport.on("GET", "/v1beta/accountSummaries", 200, _SUMMARIES)
    assert _verify()({"service_account_json": key_json}, {}) == {"identity": {
        "service_account": EMAIL, "accounts": 1, "properties": 1}}


def test_sonde_zero_propriete_est_un_refus_pas_un_vert(transport, key_json):
    from oto_mcp.connectors import verify as V

    transport.on("GET", "/v1beta/accountSummaries", 200, {})
    with pytest.raises(V.NonAutorise, match="aucune propriété"):
        _verify()({"service_account_json": key_json}, {})


def test_sonde_cle_revoquee_est_non_autorisee(transport, key_json):
    from oto_mcp.connectors import verify as V

    transport.on("POST", "/token", 400, {"error": "invalid_grant",
                                         "error_description": "Invalid JWT Signature."})
    with pytest.raises(V.NonAutorise):
        _verify()({"service_account_json": key_json}, {})


def test_sonde_json_qui_n_est_pas_une_cle_est_non_autorise(transport):
    from oto_mcp.connectors import verify as V

    with pytest.raises(V.NonAutorise, match="client OAuth"):
        _verify()({"service_account_json": '{"installed": {}}'}, {})
    assert V.couverture("google_analytics") == V.AUTH
