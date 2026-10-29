"""Connecteur Pipecorn — enrichissement de contact en cascade, et le solde.

Les tripwires génériques couvrent déjà le registre, l'éditeur, le logo, la prose
servie, la paire lire/agir et la jointure au client oto-core. Ce fichier verrouille
ce qui est PROPRE à ce module :

- un seul outil `pipecorn` (LECTURE), le verbe dans `op` ;
- l'enrichissement est ASYNCHRONE : `enrich` soumet, `result` relève ;
- ce qu'il a coûté, noté à la relève terminée seulement (`quantity`, toute clé), et une
  recherche comptée à l'envoi sur la clé partagée (`units`) — sans plafond par org ;
- la clé partagée ne sert QUE le téléphone, refusé AVANT toute dépense sinon ;
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
ID = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
FINI = {"enrichment_id": ID, "status": "finished", "phone": ["+33612345678"],
        "phone_details": [{"number": "+33612345678", "type": "mobile"}]}


@pytest.fixture()
def key(monkeypatch):
    """`mode partagé ?` — la clé que rend la cascade."""
    state = {"platform": False}

    def resolve(provider, account=None, units=1):
        assert provider == "pipecorn"
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
        cls.return_value.submit_enrichment.return_value = {
            "enrichment_id": ID, "status": "pending"}
        cls.return_value.get_enrichment.return_value = FINI
        cls.return_value.get_credits.return_value = {"credits": {"enrichment": 2500}}
        cls.return_value.get_account_info.return_value = {"account": {"plan": "growth"}}
        yield cls


# --- registre ---------------------------------------------------------------

def test_un_seul_outil_en_lecture():
    tools = _tools()
    assert set(tools) == {"pipecorn"}
    assert tools["pipecorn"].annotations.readOnlyHint is True


def test_registre_cle_partagee_sur_octroi_sans_plafond_par_org():
    # Julien, 29/10 : pas de limite par org, les crédits Tulina bornent la dépense.
    c = providers.REGISTRY["pipecorn"]
    assert c.auth_modes == {"byo_user", "byo_org", "platform"}
    assert c.platform_key_open is False and c.default_quota == 0
    assert c.grant_quota_unit == ""


# --- enrich : l'envoi asynchrone -----------------------------------------------

def test_enrich_soumet_et_rend_l_identifiant_a_relever(key, trace, client):
    out = _fn()(linkedin_url=URL, phone_type="mobile")
    client.assert_called_once_with(api_key=KEY)
    sent = client.return_value.submit_enrichment.call_args
    assert sent.args == (["phone"],)
    assert sent.kwargs["linkedin_url"] == URL and sent.kwargs["phone_type"] == "mobile"
    client.return_value.enrich_contact.assert_not_called()
    assert out["enrichment_id"] == ID and out["status"] == "pending"
    assert out["find"] == ["phone"] and "op='result'" in out["next_step"]
    # Rien n'est dépensé avant la fin : un zéro TRACÉ.
    assert trace == {"quantity": 0}


def test_enrich_sur_la_cle_partagee_compte_une_recherche(key, trace, client):
    key["platform"] = True
    _fn()(linkedin_url=URL)
    assert trace == {"quantity": 0, "units": 1}


def test_un_envoi_sans_identifiant_est_un_refus(key, trace, client):
    client.return_value.submit_enrichment.return_value = {"status": "pending"}
    with pytest.raises(McpError, match="no enrichment_id"):
        _fn()(linkedin_url=URL)


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
    client.return_value.submit_enrichment.assert_not_called()
    client.return_value.get_credits.assert_not_called()
    assert trace == {}


def test_une_entree_manquante_est_un_refus_nomme(key, trace):
    # Le vrai client (pas de faux) : son refus local devient un INVALID_PARAMS.
    with pytest.raises(McpError, match="linkedin_url"):
        _fn()(find=["phone"])
    assert trace == {}


# --- result : la relève, qui facture ----------------------------------------------

def test_en_cours_rien_n_est_facture(key, trace, client):
    client.return_value.get_enrichment.return_value = {"status": "pending"}
    out = _fn()(op="result", enrichment_id=ID, find=["phone"])
    client.return_value.get_enrichment.assert_called_once_with(ID)
    assert out["done"] is False and trace == {"quantity": 0}


def test_fini_facture_ce_qui_a_ete_trouve_et_demande(key, trace, client):
    # Le bac à sable rend aussi un email non demandé : il ne compte pas.
    client.return_value.get_enrichment.return_value = {**FINI, "email": "jane@acme.com"}
    out = _fn()(op="result", enrichment_id=ID, find=["phone"])
    assert out["done"] is True and out["pipecorn_credits"] == 30
    assert out["phone_details"] == FINI["phone_details"]
    assert trace == {"quantity": 30}


def test_fini_sans_mobile_ne_coute_rien(key, trace, client):
    client.return_value.get_enrichment.return_value = {
        "status": "finished", "phone": [], "phone_details": []}
    out = _fn()(op="result", enrichment_id=ID)
    assert out["pipecorn_credits"] == 0 and trace == {"quantity": 0}


def test_la_releve_sur_la_cle_partagee_ne_compte_aucune_recherche(key, trace, client):
    key["platform"] = True
    client.return_value.get_enrichment.return_value = FINI
    _fn()(op="result", enrichment_id=ID, find=["phone"])
    assert trace == {"quantity": 30, "units": 0}


@pytest.mark.parametrize("code, match", [
    ("insufficient_credits", "balance ran out"),
    ("workspace_limit_exceeded", "weekly credit limit"),
    ("", "no reason given"),
])
def test_un_echec_dit_pourquoi_et_ne_facture_rien(key, trace, client, code, match):
    client.return_value.get_enrichment.return_value = {"status": "failed", "code": code}
    with pytest.raises(McpError, match=match):
        _fn()(op="result", enrichment_id=ID)
    assert trace == {"quantity": 0}


def test_la_releve_exige_l_identifiant(key, client):
    with pytest.raises(McpError, match="enrichment_id"):
        _fn()(op="result")


# --- credits / account ------------------------------------------------------

def test_solde_et_compte(key, client):
    assert _fn()(op="credits") == {"credits": {"enrichment": 2500}}
    assert _fn()(op="account") == {"account": {"plan": "growth"}}


@pytest.mark.parametrize("kwargs, name", [
    ({"op": "credits", "linkedin_url": URL}, "linkedin_url"),
    ({"op": "result", "enrichment_id": ID, "linkedin_url": URL}, "linkedin_url"),
    ({"enrichment_id": ID, "linkedin_url": URL}, "enrichment_id"),
])
def test_aucun_argument_ignore_en_silence(key, client, kwargs, name):
    with pytest.raises(McpError, match=f"does not take `{name}`"):
        _fn()(**kwargs)


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
    client.return_value.submit_enrichment.side_effect = _upstream(status, body)
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
