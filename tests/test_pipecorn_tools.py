"""Connecteur Pipecorn — enrichissement de contact en cascade, et le solde.

Les tripwires génériques couvrent déjà le registre, l'éditeur, le logo, la prose
servie, la paire lire/agir et la jointure au client oto-core. Ce fichier verrouille
ce qui est PROPRE à ce module :

- un seul outil `pipecorn` (LECTURE), le verbe dans `op` ;
- chaque op appelle la BONNE méthode, avec les bons arguments, borné en temps ;
- ce que l'appel a coûté (`quantity`, toute clé ; `units` sur la clé partagée) ;
- la clé partagée ne sert QUE le téléphone, refusé AVANT toute dépense sinon, et son
  quota est réservé pour le pire cas de l'appel ;
- la traduction des refus Pipecorn (402 sur la clé partagée ≠ sur la sienne) et la sonde.

Ne tournent qu'après installation d'oto-core avec `oto.tools.pipecorn`. Toutes les
valeurs sont factices.
"""
import asyncio
from unittest.mock import patch

import pytest

from oto_mcp import providers
from oto_mcp.mcp_errors import McpError

KEY = "pc_test_fake"
URL = "https://www.linkedin.com/in/jane-doe/"
MOBILE = {"status": "finished", "phones": ["+33612345678"],
          "phone_details": [{"number": "+33612345678", "type": "mobile"}],
          "phone_status": "valid"}


@pytest.fixture()
def key(monkeypatch):
    """`(mode partagé ?, unités réservées)` — la clé que rend la cascade."""
    state = {"platform": False, "units": []}

    def resolve(provider, account=None, units=1):
        assert provider == "pipecorn"
        state["units"].append(units)
        return KEY, state["platform"]
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", resolve)
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: None)
    monkeypatch.setattr("oto_mcp.auth.hooks.current_user_sub_from_token", lambda: "sub")
    return state


@pytest.fixture()
def trace(monkeypatch):
    noted = {}
    monkeypatch.setattr("oto_mcp.session_org.note_call_trace",
                        lambda **values: noted.update(values))
    return noted


def _tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import pipecorn

    m = FastMCP("t")
    pipecorn.register(m)
    return {t.name: t for t in asyncio.run(m._list_tools())}


def _fn():
    return _tools()["pipecorn"].fn


@pytest.fixture()
def client():
    with patch("oto.tools.pipecorn.PipecornClient") as cls:
        cls.return_value.enrich_contact.return_value = MOBILE
        cls.return_value.get_credits.return_value = {"credits": {"enrichment": 2500}}
        cls.return_value.get_account_info.return_value = {"account": {"plan": "growth"}}
        yield cls


# --- registre ---------------------------------------------------------------

def test_un_seul_outil_en_lecture():
    tools = _tools()
    assert set(tools) == {"pipecorn"}
    assert tools["pipecorn"].annotations.readOnlyHint is True


def test_registre_cle_partagee_sur_octroi_avec_quota_en_credits():
    c = providers.REGISTRY["pipecorn"]
    assert c.auth_modes == {"byo_user", "byo_org", "platform"}
    assert c.platform_key_open is False and c.default_quota == 0
    assert c.grant_quota_unit == "Pipecorn credits"


# --- enrich -----------------------------------------------------------------

def test_telephone_par_defaut_borne_en_temps(key, trace, client):
    out = _fn()(linkedin_url=URL, phone_type="mobile")
    client.assert_called_once_with(api_key=KEY)
    kwargs = client.return_value.enrich_contact.call_args
    assert kwargs.args == (["phone"],)
    assert kwargs.kwargs["linkedin_url"] == URL and kwargs.kwargs["phone_type"] == "mobile"
    assert kwargs.kwargs["timeout"] == (10, 30)
    assert out["phones"] == ["+33612345678"] and out["pipecorn_credits"] == 30
    # Sa propre clé : la quantité facturable est notée, pas le quota d'un octroi.
    assert trace == {"quantity": 30}
    assert key["units"] == [30]


def test_un_echec_ne_coute_rien(key, trace, client):
    client.return_value.enrich_contact.return_value = {
        "status": "finished", "phones": [], "phone_details": [], "phone_status": "not_found"}
    out = _fn()(linkedin_url=URL)
    assert out["pipecorn_credits"] == 0 and trace == {"quantity": 0}


def test_email_et_telephone_reservent_le_pire_cas(key, trace, client):
    client.return_value.enrich_contact.return_value = {**MOBILE, "email": "jane@acme.com"}
    out = _fn()(find=["email", "phone"], linkedin_url=URL)
    assert key["units"] == [33] and out["pipecorn_credits"] == 33


def test_cle_partagee_telephone_note_quantite_et_unites(key, trace, client):
    key["platform"] = True
    _fn()(linkedin_url=URL, phone_type="mobile")
    assert trace == {"quantity": 30, "units": 30}


@pytest.mark.parametrize("kwargs", [
    {"find": ["email"], "linkedin_url": URL},
    {"find": ["phone", "personal_email"], "linkedin_url": URL},
    {"op": "credits"},
    {"op": "account"},
])
def test_cle_partagee_refuse_tout_sauf_le_telephone_avant_depense(key, trace, client, kwargs):
    key["platform"] = True
    with pytest.raises(McpError, match="needs your own Pipecorn key"):
        _fn()(**kwargs)
    client.return_value.enrich_contact.assert_not_called()
    client.return_value.get_credits.assert_not_called()
    assert trace == {}


def test_une_entree_manquante_est_un_refus_nomme(key, trace):
    # Le vrai client (pas de faux) : son refus local devient un INVALID_PARAMS.
    with pytest.raises(McpError, match="linkedin_url"):
        _fn()(find=["phone"])
    assert trace == {}


# --- credits / account ------------------------------------------------------

def test_solde_et_compte(key, client):
    assert _fn()(op="credits") == {"credits": {"enrichment": 2500}}
    assert _fn()(op="account") == {"account": {"plan": "growth"}}


def test_aucun_argument_ignore_en_silence(key, client):
    with pytest.raises(McpError, match="does not take `linkedin_url`"):
        _fn()(op="credits", linkedin_url=URL)


# --- refus de Pipecorn ------------------------------------------------------

def _upstream(status, body):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status_code=status, body=body, service="pipecorn")


@pytest.mark.parametrize("platform, status, body, match", [
    (False, 402, {"code": "insufficient_credits"}, "Top up in Pipecorn"),
    (True, 402, {"code": "insufficient_credits"}, "shared key's credit balance is empty"),
    (False, 403, {"code": "workspace_limit_exceeded"}, "weekly credit limit"),
    (False, 401, {"error": "Invalid API key"}, "rejected the API key"),
    (False, 422, {"error": "Linkedin URL is required"}, "Linkedin URL is required"),
    (False, 500, {"error": "An error occurred during enrichment"}, "could not finish"),
])
def test_traduction_des_refus(key, trace, client, platform, status, body, match):
    key["platform"] = platform
    client.return_value.enrich_contact.side_effect = _upstream(status, body)
    with pytest.raises(McpError, match=match):
        _fn()(linkedin_url=URL)
    assert trace == {}


# --- sonde ------------------------------------------------------------------

def test_sonde(client):
    from oto_mcp.connectors import verify
    from oto_mcp.tools import pipecorn

    client.return_value.probe.return_value = {"credits": {"enrichment": 0}}
    with pytest.raises(verify.QuotaEpuise):
        pipecorn._verify({"key": KEY})
    client.return_value.probe.return_value = {"credits": {"enrichment": 12}}
    pipecorn._verify({"key": KEY})
    client.return_value.probe.side_effect = _upstream(401, {"error": "Invalid API key"})
    with pytest.raises(verify.NonAutorise):
        pipecorn._verify({"key": KEY})
