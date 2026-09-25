"""Crédits épuisés vus AU MOMENT DE L'APPEL : refus nommé et clé au rouge (25/09/2026).

Mesuré : un 402 amont (theirstack, AI Ark…) remontait à l'agent en `invalid_input`
(« corrige ton appel ») ou `upstream_4xx`, et la clé qui l'avait servi restait verte
sur sa fiche. Personne ne rechargeait : 13 signaux d'usage. Désormais :

- le 402 est classé `quota_exhausted`, non rejouable, même sous la `McpError` curée
  qu'un outil lève dans son `except` (le message curé est gardé) ;
- la clé qui a servi l'appel est marquée `no_quota` (`meta.health_verdict`), sauf clé
  plateforme ou tenant — jamais peinte en rouge pour tout le monde ;
- le premier appel réussi sur cette clé lève la marque, une seule fois par process ;
- la carte connecteur dit « recharge », pas « repose la clé ».

Aucune base : le coffre est stubé au seam `credentials_store`.
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp import credentials_store, error_taxonomy, session_org
from oto_mcp.connectors import health, readiness
from oto_mcp.mcp_errors import McpError
from oto_mcp.middleware.error_envelope import ErrorEnvelopeMiddleware

BYO = ("member", "7:sub-a", "theirstack", "")
PLATEFORME = ("platform", "defaut", "theirstack", "")


class _Amont(Exception):
    """Une erreur amont typée comme `UpstreamHTTPError` (`.status_code`)."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _outil_qui_cure(status: int):
    """Le patron de theirstack/AI Ark : l'outil attrape l'erreur amont et lève une
    `McpError` curée DANS son `except` (la cause reste dans `__context__`)."""
    try:
        raise _Amont(status)
    except _Amont:
        raise McpError(ErrorData(code=INVALID_PARAMS,
                                 message="Crédits épuisés — recharge le compte."))


@pytest.fixture
def coffre(monkeypatch):
    """Coffre en mémoire : ce que `update_meta` et l'effacement conditionnel écrivent."""
    rec = {"meta": [], "effacements": []}
    health._SANS_MARQUE.clear()

    def _update_meta(et, eid, connector, account, patch, conn=None):
        rec["meta"].append(((et, eid, connector, account), patch))
        return True

    def _clear(et, eid, connector, account, *, verdict):
        rec["effacements"].append(((et, eid, connector, account), verdict))
        return True

    monkeypatch.setattr(credentials_store, "update_meta", _update_meta)
    monkeypatch.setattr(credentials_store, "clear_health_if_verdict", _clear)
    yield rec
    health._SANS_MARQUE.clear()


async def _appel(trace: dict, call_next):
    """Un appel d'outil traversant l'enveloppe, relevé posé comme par `CallContext`."""
    tok = session_org.set_call_trace(trace)
    try:
        return await ErrorEnvelopeMiddleware().on_call_tool(object(), call_next)
    finally:
        session_org.reset_call_trace(tok)


# --- la taxonomie ---------------------------------------------------------------

def test_un_402_sous_une_mcperror_curee_est_quota_exhausted():
    """Sans le cran (0), la `McpError` curée l'emportait : `invalid_input`."""
    with pytest.raises(McpError) as exc:
        _outil_qui_cure(402)
    info = error_taxonomy.classify(exc.value)
    assert info.code == "quota_exhausted"
    assert info.retryable is False
    assert info.message == "Crédits épuisés — recharge le compte."
    assert "recharge" in info.hint


def test_un_403_ordinaire_reste_not_authorized():
    info = error_taxonomy.classify(_Amont(403))
    assert info.code == "not_authorized"


# --- l'enveloppe : marquer, puis effacer -----------------------------------------

def test_un_402_sur_une_cle_byo_marque_la_cle_no_quota(coffre):
    async def _echoue(ctx):
        _outil_qui_cure(402)

    trace = {"credential_row": BYO, "resolved_connector": "theirstack"}
    with pytest.raises(McpError) as exc:
        asyncio.run(_appel(trace, _echoue))
    oto = exc.value.error.data["oto"]
    assert oto["code"] == "quota_exhausted" and oto["retryable"] is False
    assert "theirstack" in oto["hint"]
    assert len(coffre["meta"]) == 1
    ligne, patch = coffre["meta"][0]
    assert ligne == BYO
    assert patch["health_ko"] is True and patch["health_verdict"] == "no_quota"


def test_un_402_sur_une_cle_plateforme_ne_marque_rien(coffre):
    """La clé plateforme sert tout le monde : l'agent reçoit le refus nommé, la clé
    n'est pas peinte en rouge pour les autres orgs."""
    async def _echoue(ctx):
        raise _Amont(402)

    with pytest.raises(McpError) as exc:
        asyncio.run(_appel({"credential_row": PLATEFORME}, _echoue))
    assert exc.value.error.data["oto"]["code"] == "quota_exhausted"
    assert coffre["meta"] == [] and coffre["effacements"] == []


def test_un_403_ne_marque_pas_la_cle(coffre):
    async def _echoue(ctx):
        raise _Amont(403)

    with pytest.raises(McpError):
        asyncio.run(_appel({"credential_row": BYO}, _echoue))
    assert coffre["meta"] == []


def test_le_premier_succes_efface_la_marque_une_seule_fois(coffre):
    async def _echoue(ctx):
        raise _Amont(402)

    async def _reussit(ctx):
        return "ok"

    with pytest.raises(McpError):
        asyncio.run(_appel({"credential_row": BYO}, _echoue))
    assert asyncio.run(_appel({"credential_row": BYO}, _reussit)) == "ok"
    assert asyncio.run(_appel({"credential_row": BYO}, _reussit)) == "ok"
    assert coffre["effacements"] == [(BYO, "no_quota")], \
        "un succès doit lever la marque, et une seule écriture par clé et par process"


def test_un_appel_sans_cle_servie_ne_touche_pas_au_coffre(coffre):
    async def _reussit(ctx):
        return "ok"

    asyncio.run(_appel({}, _reussit))
    assert coffre["meta"] == [] and coffre["effacements"] == []


# --- la carte connecteur -----------------------------------------------------------

@pytest.fixture
def carte(monkeypatch):
    from oto_mcp import access, providers, status_hints

    rec = {"health": None}
    monkeypatch.setattr(access, "paid_option_for", lambda c: None)
    monkeypatch.setattr(access, "option_open", lambda sub, c, org=None: True)
    monkeypatch.setattr(access, "credential_mode_for",
                        lambda sub, c, org=None, group=None: "user")
    monkeypatch.setattr(providers, "credential_provider", lambda c: c)
    monkeypatch.setattr(status_hints, "pending_action",
                        lambda c, sub, org, group, st: None)
    monkeypatch.setattr(access, "credential_rejection_for",
                        lambda sub, provider, *, org=None, group=None: rec["health"],
                        raising=False)
    return rec


def test_la_carte_dit_recharge_sur_une_cle_a_sec(carte):
    carte["health"] = f"{credentials_store.NO_QUOTA_REASON_PREFIX} : HTTP 402"
    diag = readiness.diagnose("u1", "theirstack", org=7, group=None)
    assert diag is not None and diag.reason == readiness.CREDENTIAL_REJECTED
    assert "Recharge" in diag.next_step
    assert "Repose-la" not in diag.next_step


def test_la_carte_dit_repose_sur_une_cle_rejetee(carte):
    carte["health"] = "invalid_grant"
    diag = readiness.diagnose("u1", "theirstack", org=7, group=None)
    assert "Repose-la" in diag.next_step


# --- la sonde theirstack ---------------------------------------------------------

def _sonde(monkeypatch, solde):
    from oto.tools.theirstack import client as ts_client
    from oto_mcp.tools import theirstack

    monkeypatch.setattr(ts_client.TheirStackClient, "credit_balance",
                        lambda self: solde)
    return theirstack._verify({"key": "k"})


def test_la_sonde_theirstack_a_sec_rend_no_quota(monkeypatch):
    from oto_mcp.connectors import verify as connector_verify

    with pytest.raises(connector_verify.QuotaEpuise) as exc:
        _sonde(monkeypatch, {"api_credits": 0, "used_api_credits": 0})
    assert connector_verify.classer(exc.value) == connector_verify.NO_QUOTA


def test_la_sonde_theirstack_rend_son_solde(monkeypatch):
    out = _sonde(monkeypatch, {"api_credits": 50, "used_api_credits": 3})
    assert out["quota"]["api_credits"] == 50
