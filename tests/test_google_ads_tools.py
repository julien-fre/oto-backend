"""Tools `google_ads_*` (huitième service Google, 2026-10-08).

Ce que ce fichier verrouille, parce que le scope `adwords` PERMET de modifier des
campagnes — Google ne tient pas la lecture seule à notre place :
1. LECTURE SEULE — seules trois URLs de lecture partent ; une requête qui n'est pas un
   `SELECT … FROM …` est refusée sans qu'aucun appel ne parte ;
2. le JETON — dans l'en-tête `Authorization`, jamais dans l'URL ; aucun developer
   token (retiré par Google le 2026-09-09) ; `login-customer-id` seulement s'il est
   donné ; le consentement demandé est celui du service `google_ads` ;
3. la PAGINATION — notre `page_token` tranche les pages de 10 000 lignes de Google
   sans rien perdre entre deux tranches, et ne sert qu'à la même requête ;
4. les refus Google NOMMÉS (code d'erreur Google Ads, pas le texte brut).

Le VRAI client d'oto-core (`oto.tools.google.ads`) sur une `requests.Session` à
transport simulé, au seam `_client_for_user` : ni serveur, ni coffre, ni Google.
"""
import asyncio
import json

import pytest
import requests
from requests.adapters import BaseAdapter
from mcp.types import INVALID_PARAMS

from oto_mcp.mcp_errors import McpError

API = "https://googleads.googleapis.com/v25"


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import google_ads as T

    m = FastMCP("t")
    T.register(m)
    fn = asyncio.run(m.get_tool(name)).fn
    return lambda **kw: asyncio.run(fn(**kw))


class _Google(BaseAdapter):
    """Faux Google Ads : enregistre chaque requête, répond par la file `replies`
    (ou par `handler(corps)` s'il est posé)."""

    def __init__(self):
        super().__init__()
        self.requests: list[requests.PreparedRequest] = []
        self.replies: list = []
        self.handler = None

    def send(self, request, **kwargs):
        self.requests.append(request)
        if self.handler:
            status, body = 200, self.handler(json.loads(request.body))
        else:
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            status, body = reply if isinstance(reply, tuple) else (200, reply)
        resp = requests.Response()
        resp.status_code = status
        resp._content = json.dumps(body).encode()
        resp.headers["Content-Type"] = "application/json"
        resp.url, resp.request = request.url, request
        return resp

    def close(self):
        pass

    def bodies(self):
        return [json.loads(r.body) if r.body else None for r in self.requests]


@pytest.fixture
def google(monkeypatch):
    from oto.tools.google.ads import GoogleAdsClient
    from oto_mcp.tools import google_ads as T

    g = _Google()
    s = requests.Session()
    s.mount("https://", g)
    monkeypatch.setattr(T, "_client_for_user",
                        lambda account=None: GoogleAdsClient("ya29.jeton", session=s))
    return g


def _rows(n, start=0):
    return [{"campaign": {"id": str(i), "name": f"c{i}"},
             "metrics": {"costMicros": str(i * 1000)}} for i in range(start, start + n)]


MASK = "campaign.id,campaign.name,metrics.costMicros"
Q = "SELECT campaign.id, campaign.name, metrics.cost_micros FROM campaign"


# --- 1. lecture seule ---------------------------------------------------------

@pytest.mark.parametrize("query", [
    "", "   ", "DELETE FROM campaign", "UPDATE campaign SET name = 'x'",
    "campaign.id FROM campaign", "SELECT campaign.id", "mutate campaigns",
])
def test_une_requete_qui_nest_pas_un_select_gaql_est_refusee_sans_appel(google, query):
    google.replies = [{"results": []}]
    with pytest.raises(McpError) as e:
        _tool("google_ads_search")(customer_id="1234567890", query=query)
    assert e.value.error.code == INVALID_PARAMS
    assert google.requests == []


def test_seules_trois_urls_de_lecture_partent(google):
    google.replies = [{"resourceNames": ["customers/1234567890"],
                       "results": [{"name": "campaign.id", "selectable": True}],
                       "fieldMask": "campaign.id"}]
    _tool("google_ads_customers")()
    _tool("google_ads_search")(customer_id="1234567890",
                               query="SELECT campaign.id FROM campaign")
    _tool("google_ads_fields")(resource="campaign")
    urls = {(r.method, str(r.url)) for r in google.requests}
    assert urls == {
        ("GET", f"{API}/customers:listAccessibleCustomers"),
        ("POST", f"{API}/customers/1234567890/googleAds:search"),
        ("POST", f"{API}/googleAdsFields:search"),
    }
    assert not any("mutate" in u for _, u in urls)


# --- 2. jeton et en-têtes -----------------------------------------------------

def test_le_jeton_part_en_entete_sans_developer_token(google):
    google.replies = [{"results": [], "fieldMask": MASK}]
    _tool("google_ads_search")(customer_id="123-456-7890", query=Q)
    req = google.requests[0]
    assert req.headers["authorization"] == "Bearer ya29.jeton"
    assert "developer-token" not in req.headers
    assert "login-customer-id" not in req.headers
    assert "ya29" not in str(req.url)
    assert str(req.url) == f"{API}/customers/1234567890/googleAds:search"


def test_login_customer_id_part_en_entete_normalise(google):
    google.replies = [{"results": [], "fieldMask": MASK}]
    _tool("google_ads_search")(customer_id="1234567890", query=Q,
                               login_customer_id="987-654-3210")
    assert google.requests[0].headers["login-customer-id"] == "9876543210"


@pytest.mark.parametrize("cid", ["123", "12345678901", "abc-def-ghij", "1234567890/x"])
def test_un_customer_id_hors_forme_est_refuse_sans_appel(google, cid):
    google.replies = [{"results": []}]
    with pytest.raises(McpError, match="10 digits"):
        _tool("google_ads_search")(customer_id=cid, query=Q)
    assert google.requests == []


def test_le_jeton_est_celui_du_service_google_ads(monkeypatch):
    from oto_mcp.tools import google_ads as T

    vus = []

    class _Creds:
        token = "ya29.x"

    def credentials_for(sub, account=None, service=None):
        vus.append((sub, account, service))
        return _Creds()

    monkeypatch.setattr(T.access, "current_user_sub_or_raise", lambda: "u1")
    monkeypatch.setattr(T.google_oauth, "credentials_for", credentials_for)
    assert T._client_for_user("a@x.test")._token == "ya29.x"
    assert vus == [("u1", "a@x.test", "google_ads")]


def test_un_compte_qui_na_pas_autorise_google_ads_est_un_refus_nomme(monkeypatch):
    from oto_mcp.tools import google_ads as T

    def credentials_for(sub, account=None, service=None):
        raise RuntimeError("has not yet authorized Google Ads: connect Google Ads")

    monkeypatch.setattr(T.access, "current_user_sub_or_raise", lambda: "u1")
    monkeypatch.setattr(T.google_oauth, "credentials_for", credentials_for)
    with pytest.raises(McpError, match="Google Ads"):
        T._client_for_user()


# --- 3. pagination ------------------------------------------------------------

def test_les_colonnes_suivent_le_field_mask_et_les_lignes_leur_ordre(google):
    google.replies = [{"results": [{"campaign": {"id": "7", "name": "Marque"}}],
                       "fieldMask": MASK, "totalResultsCount": "1"}]
    out = _tool("google_ads_search")(customer_id="1234567890", query=Q)
    assert out["columns"] == ["campaign.id", "campaign.name", "metrics.costMicros"]
    assert out["rows"] == [["7", "Marque", None]]
    assert out["total_rows"] == 1 and "page_token" not in out


def test_notre_curseur_tranche_la_page_google_sans_rien_perdre(google):
    page1 = {"results": _rows(450), "fieldMask": MASK, "nextPageToken": "G2"}
    page2 = {"results": _rows(30, 450), "fieldMask": MASK}
    search = _tool("google_ads_search")
    google.replies = [page1, page1, page1, page2]
    vus, token = [], None
    for _ in range(4):
        kw = {"page_token": token} if token else {}
        out = search(customer_id="1234567890", query=Q, max_rows=200, **kw)
        vus += [r[0] for r in out["rows"]]
        token = out.get("page_token")
    assert vus == [str(i) for i in range(480)] and token is None
    # Google ne reçoit SA page suivante qu'une fois la sienne entièrement servie.
    assert [b.get("pageToken") for b in google.bodies()] == [None, None, None, "G2"]
    assert all(b["query"] == Q for b in google.bodies())


def test_max_rows_est_plafonne_a_1000(google):
    google.replies = [{"results": _rows(1500), "fieldMask": MASK}]
    out = _tool("google_ads_search")(customer_id="1234567890", query=Q, max_rows=5000)
    assert out["row_count"] == 1000 and "page_token" in out


def test_un_curseur_ne_sert_qu_a_sa_requete(google):
    google.replies = [{"results": _rows(300), "fieldMask": MASK}]
    out = _tool("google_ads_search")(customer_id="1234567890", query=Q, max_rows=10)
    google.requests.clear()
    with pytest.raises(McpError, match="another query"):
        _tool("google_ads_search")(customer_id="1234567890",
                                   query="SELECT campaign.id FROM campaign",
                                   page_token=out["page_token"])
    with pytest.raises(McpError, match="page_token"):
        _tool("google_ads_search")(customer_id="1234567890", query=Q, page_token="n'importe")
    assert google.requests == []


# --- 4. refus nommés ----------------------------------------------------------

def _ads_error(http, status, family, code, message="m", request_id="req-1"):
    return (http, {"error": {"code": http, "message": "Request contains an invalid argument.",
                             "status": status, "details": [{
                                 "@type": "type.googleapis.com/google.ads.googleads.v25"
                                          ".errors.GoogleAdsFailure",
                                 "errors": [{"errorCode": {family: code},
                                             "message": message}],
                                 "requestId": request_id}]}})


@pytest.mark.parametrize("reply, attendu", [
    (_ads_error(403, "PERMISSION_DENIED", "authorizationError",
                "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"), "only approved for TEST"),
    (_ads_error(403, "PERMISSION_DENIED", "authorizationError", "USER_PERMISSION_DENIED"),
     "login_customer_id"),
    (_ads_error(401, "UNAUTHENTICATED", "authenticationError", "NOT_ADS_USER"),
     "not a user of any Google Ads account"),
    (_ads_error(403, "PERMISSION_DENIED", "authorizationError", "CUSTOMER_NOT_ENABLED"),
     "not enabled"),
    (_ads_error(400, "INVALID_ARGUMENT", "queryError", "UNRECOGNIZED_FIELD",
                "Unrecognized field in the query: 'campaign.nom'."), "google_ads_fields"),
    (_ads_error(429, "RESOURCE_EXHAUSTED", "quotaError", "RESOURCE_EXHAUSTED"), "quota"),
    ((403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
                      "message": "Google Ads API has not been used in project 1 before",
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                   "reason": "SERVICE_DISABLED"}]}}), "not enabled in the"),
    ((503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "x"}}),
     "temporarily unavailable"),
])
def test_les_refus_google_sont_nommes(google, reply, attendu):
    google.replies = [reply]
    with pytest.raises(McpError) as e:
        _tool("google_ads_search")(customer_id="1234567890", query=Q)
    assert e.value.error.code == INVALID_PARAMS
    assert attendu in e.value.error.message


def test_le_refus_porte_lidentifiant_de_requete_google(google):
    google.replies = [_ads_error(400, "INVALID_ARGUMENT", "queryError", "BAD_FIELD_NAME",
                                 request_id="abc123")]
    with pytest.raises(McpError, match="abc123"):
        _tool("google_ads_search")(customer_id="1234567890", query=Q)


# --- comptes et champs --------------------------------------------------------

def test_les_comptes_accessibles_perdent_leur_prefixe(google):
    google.replies = [{"resourceNames": ["customers/1234567890", "customers/1112223334"]}]
    assert _tool("google_ads_customers")() == {"customer_ids": ["1234567890", "1112223334"]}


def test_les_champs_sont_ranges_par_famille(google):
    own = {"results": [
        {"name": "campaign.id", "category": "ATTRIBUTE", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "campaign.name", "category": "ATTRIBUTE", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "campaign.url_custom_parameters", "category": "ATTRIBUTE",
         "selectable": True}]}
    linked = {"results": [
        {"name": "metrics.clicks", "category": "METRIC", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "segments.date", "category": "SEGMENT", "selectable": True,
         "filterable": True, "sortable": True},
        {"name": "customer.currency_code", "category": "ATTRIBUTE", "selectable": True,
         "filterable": True, "sortable": True}]}

    google.handler = lambda body: own if "LIKE 'campaign.%'" in body["query"] else linked
    out = _tool("google_ads_fields")(resource="campaign")
    assert out["attributes"] == ["campaign.id", "campaign.name",
                                 "campaign.url_custom_parameters"]
    assert out["metrics"] == ["metrics.clicks"] and out["segments"] == ["segments.date"]
    assert out["related"] == ["customer.currency_code"]
    assert out["not_filterable"] == ["campaign.url_custom_parameters"]


def test_une_ressource_inconnue_est_un_refus_nomme(google):
    google.replies = [{"results": []}]
    with pytest.raises(McpError, match="no resource `campagne`"):
        _tool("google_ads_fields")(resource="campagne")


@pytest.mark.parametrize("resource", ["Campaign", "campaign' OR 1", "ad-group", ""])
def test_un_nom_de_ressource_hors_forme_est_refuse_sans_appel(google, resource):
    google.replies = [{"results": []}]
    with pytest.raises(McpError, match="snake_case"):
        _tool("google_ads_fields")(resource=resource)
    assert google.requests == []
