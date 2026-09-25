"""Connecteur BLS (salaires US, OEWS) — verrouille : l'entrée registre (open data,
AUCUN credential), la surface MCP (un outil, décrit), la jointure tool↔client
oto-core, et le contrat tenu à travers le tool layer sur un amont mocké :
identifiants de série des trois types de zone, normalisation du SOC (suffixe O*NET
retiré ET dit), découpage au plafond de 25 séries, lignes « No Data Available »
(pas des erreurs), valeurs `-`/notes jamais devinées, refus rendus actionnables.
"""
import asyncio
from unittest.mock import patch

import pytest
from oto_mcp import providers
from oto_mcp.mcp_errors import McpError
from oto_mcp.tool_visibility import TESTABLE_NAMESPACES, namespace_of


def _tool(name="bls_oews_wages"):
    from fastmcp import FastMCP
    from oto_mcp.tools import bls as bls_tool

    m = FastMCP("t")
    bls_tool.register(m)
    return asyncio.run(m.get_tool(name))


def _call(**kwargs):
    return _tool().fn(**kwargs)


@pytest.fixture(autouse=True)
def _no_platform_key(monkeypatch):
    monkeypatch.delenv("BLS_API_KEY", raising=False)
    monkeypatch.setattr("oto.tools.bls.client.get_secret", lambda name, default=None: None)


# --- registre -----------------------------------------------------------------

def test_bls_is_open_data_without_credential():
    c = providers.REGISTRY["bls"]
    assert c.kind == "tools" and c.family == "open-data"
    assert c.secret_kind == "none" and not c.keyed and c.auth_method == "none"
    assert "bls" not in providers.KEY_PROVIDERS
    assert "bls" not in providers.CREDENTIAL_PROVIDERS
    assert c.default_active is False


def test_bls_is_not_testable_from_dashboard():
    # Le quota journalier sans clé est partagé par toute la plateforme : un bouton
    # « tester » le dépenserait pour tout le monde.
    assert "bls" not in TESTABLE_NAMESPACES


def test_bls_doc_is_served():
    kinds = [s.kind for s in providers.REGISTRY["bls"].doc_sections]
    assert "usage" in kinds and "prerequisite" not in kinds


# --- surface MCP --------------------------------------------------------------

def test_tool_registers_under_namespace_with_description():
    t = _tool()
    assert namespace_of(t.name) == "bls"
    # Régression du piège f-string-docstring (FastMCP sert alors un outil muet).
    assert t.description and "SOC" in t.description
    assert "Args:" not in t.description


def test_client_exposes_methods_called_by_tools():
    from oto.tools.bls.client import BLSClient
    assert callable(getattr(BLSClient, "oews_wages", None))


# --- contrat à travers le tool layer (amont mocké) -----------------------------

class _Resp:
    def __init__(self, body, status=200):
        self.status_code, self._body = status, body
        self.content, self.text = b"x", str(body)

    def json(self):
        return self._body


def _point(value, footnotes=None):
    return {"year": "2025", "period": "A01", "value": value, "footnotes": footnotes or [{}]}


def _upstream(values=None, messages=None, status="REQUEST_SUCCEEDED", http=200):
    """Faux BLS : rend `values[série]` (défaut "100") ; `None` = série sans donnée."""
    calls = []

    def post(self, url, **kwargs):
        ids = kwargs["json"]["seriesid"]
        calls.append(kwargs["json"])
        series = []
        for sid in ids:
            v = (values or {}).get(sid, _point("100"))
            series.append({"seriesID": sid, "data": [v] if v else []})
        return _Resp({"status": status, "message": messages or [],
                      "Results": {"series": series}}, http)

    return calls, patch("oto.tools.bls.client.requests.Session.post", post)


def test_series_ids_for_the_three_area_types():
    calls, p = _upstream()
    with p:
        out = _call(soc="15-1299", areas=["US", "Illinois", "16980"])
    assert len(calls) == 1 and out["requests"] == 1
    ids = set(calls[0]["seriesid"])
    assert len(ids) == 21
    assert {"OEUN000000000000015129915",          # national, P90
            "OEUS170000000000015129915",          # État (FIPS 17), P90
            "OEUM001698000000015129913",          # aire métropolitaine (CBSA 16980), médiane
            "OEUN000000000000015129901"} <= ids   # emploi
    us, il, chi = out["areas"]
    assert (us["area_type"], il["area_code"], chi["area_code"]) == ("N", "1700000", "0016980")
    assert chi["series_ids"]["p90"] == "OEUM001698000000015129915"
    assert "registrationkey" not in calls[0]


@pytest.mark.parametrize("soc", ["15-1299", "151299"])
def test_plain_soc_carries_no_note(soc):
    _, p = _upstream()
    with p:
        out = _call(soc=soc)
    assert out["soc"] == "15-1299" and "note" not in out
    assert out["source"].startswith("U.S. Bureau of Labor Statistics")
    assert out["year"] == "2025" and [a["area"] for a in out["areas"]] == ["US"]


def test_onet_suffix_is_stripped_and_said():
    calls, p = _upstream()
    with p:
        out = _call(soc="15-1299.08", areas=["US"])
    assert all(sid[17:23] == "151299" for sid in calls[0]["seriesid"])
    assert "'.08' stripped" in out["note"] and "15-1299.08" in out["note"]
    assert "onet_suffix_stripped" not in out


def test_chunks_over_25_series():
    calls, p = _upstream()
    with p:
        out = _call(soc="151299", areas=["US", "IL", "CA", "NY", "TX", "WA", "35620"])
    assert [len(c["seriesid"]) for c in calls] == [21, 21, 7]
    assert out["requests"] == 3 and len(out["areas"]) == 7


def test_no_data_available_lines_are_not_errors():
    p90 = "OEUN000000000000015129915"
    _, p = _upstream(values={p90: _point("188470")},
                     messages=[f"No Data Available for Series {p90} Year: 2023",
                               f"No Data Available for Series {p90} Year: 2024"])
    with p:
        out = _call(soc="15-1299")
    assert out["messages"] == []
    assert out["areas"][0]["percentiles"]["p90"] == 188470


def test_series_without_data_is_listed_missing():
    med = "OEUM001698000000015129913"
    _, p = _upstream(values={med: None},
                     messages=[f"Series does not exist for Series {med}"])
    with p:
        out = _call(soc="15-1299", areas=["16980"])
    assert out["areas"][0]["missing"] == ["p50"]
    assert out["areas"][0]["percentiles"]["p50"] is None
    assert out["messages"] == [f"Series does not exist for Series {med}"]


def test_dash_value_keeps_raw_and_footnote():
    p90 = "OEUN000000000000011101115"
    note = {"code": "5", "text": "This wage is equal to or greater than $115.00 per "
                                 "hour or $239,200 per year."}
    _, p = _upstream(values={p90: _point("-", [note])})
    with p:
        area = _call(soc="11-1011")["areas"][0]
    assert area["percentiles"]["p90"] is None and area["raw"] == {"p90": "-"}
    assert area["footnotes"] == [{"measure": "p90", **note}]
    assert area["percentiles"]["p50"] == 100      # les autres mesures restent lues


@pytest.mark.parametrize("kwargs,fragment", [
    ({"soc": "software developer"}, "code SOC invalide"),
    ({"soc": "15-1299", "areas": ["Chicago"]}, "CBSA"),
    ({"soc": "15-1299", "areas": []}, "vide"),
    ({"soc": "15-1299", "areas": ["US"] * 13}, "12 au plus"),
])
def test_bad_input_refused_before_any_call(kwargs, fragment):
    calls, p = _upstream()
    with p, pytest.raises(McpError) as e:
        _call(**kwargs)
    assert fragment in str(e.value) and calls == []


def test_daily_quota_refusal_is_actionable():
    _, p = _upstream(status="REQUEST_NOT_PROCESSED",
                     messages=["Request could not be serviced, as the daily threshold "
                               "for total number of requests allocated to the user has "
                               "been reached."])
    with p, pytest.raises(McpError) as e:
        _call(soc="15-1299")
    assert "quota journalier" in str(e.value) and "daily threshold" in str(e.value)


def test_upstream_http_error_is_translated():
    _, p = _upstream(http=503)
    with p, pytest.raises(McpError) as e:
        _call(soc="15-1299")
    assert "indisponible" in str(e.value)


def test_tool_layer_with_patched_client_class():
    with patch("oto.tools.bls.client.BLSClient") as client_cls:
        client_cls.return_value.oews_wages.return_value = {
            "soc": "15-1299", "onet_suffix_stripped": "00", "year": "2025",
            "requests": 1, "areas": [], "messages": []}
        out = _call(soc="15-1299.00", areas=["US"])
    client_cls.return_value.oews_wages.assert_called_once_with("15-1299.00", ["US"])
    assert out["note"].endswith("cover all of 15-1299.")
