"""Clé REFUSÉE vue au moment de l'appel : la clé servie passe au rouge (signal oto #1168).

Mesuré le 07/10/2026 : 143 échecs de connecteurs en 7 jours, et aucune clé kaspr,
cloro, unipile, lemlist ni jev marquée `meta.health_ko`. Seule la sonde (`op=verify`)
et quelques modules peignaient une clé refusée ; à l'appel, seul le compte à sec
(`quota_exhausted`) marquait. L'alerte des clés en panne, qui lit cette marque, restait
aveugle sur une clé morte qu'un agent programmé heurtait chaque heure.

Désormais :
- un 401 amont, même sous la `McpError` curée d'un outil, marque la clé servie
  `unauthorized`, avec `health_source = "call"` ;
- un connecteur peut déclarer son verdict (`credential_rejected`), qui l'emporte ;
- un 403 seul, une 5xx, une entrée invalide ne marquent pas ;
- une clé plateforme ou tenant n'est jamais peinte ;
- le premier appel réussi lève une marque d'appel, jamais une marque de la sonde.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp import credentials_store, error_taxonomy, session_org
from oto_mcp.connectors import health
from oto_mcp.mcp_errors import McpError
from oto_mcp.middleware.error_envelope import ErrorEnvelopeMiddleware

BYO = ("org", "7", "lemlist", "")
PLATEFORME = ("platform", "defaut", "jev", "")
TENANT = ("tenant", "un-tenant", "kaspr", "")


class _Amont(Exception):
    """Une erreur amont typée comme `UpstreamHTTPError` (`.status_code`)."""

    def __init__(self, status_code: int, credential_rejected=None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        if credential_rejected is not None:
            self.credential_rejected = credential_rejected


def _outil_qui_cure(status: int):
    """Le patron de kaspr : l'outil attrape l'erreur amont et lève une `McpError`
    curée DANS son `except` — la cause reste dans `__context__`."""
    try:
        raise _Amont(status)
    except _Amont:
        raise McpError(ErrorData(code=INVALID_PARAMS,
                                 message="Kaspr could not enrich `x`. Check the profile."))


# --- la taxonomie -----------------------------------------------------------------

def test_un_401_sous_une_mcperror_curee_est_un_refus_de_cle():
    with pytest.raises(McpError) as exc:
        _outil_qui_cure(401)
    assert error_taxonomy.credential_rejected_in_chain(exc.value) is True


@pytest.mark.parametrize("status", [403, 404, 429, 500, 502])
def test_les_autres_statuts_ne_sont_pas_un_refus_de_cle(status):
    assert error_taxonomy.credential_rejected_in_chain(_Amont(status)) is False


def test_le_connecteur_declare_son_verdict_et_il_l_emporte():
    """Un 403 qu'un connecteur sait être « clé morte » ; un 401 qu'il sait ne pas l'être."""
    assert error_taxonomy.credential_rejected_in_chain(
        _Amont(403, credential_rejected=True)) is True
    assert error_taxonomy.credential_rejected_in_chain(
        _Amont(401, credential_rejected=False)) is False


def test_une_erreur_sans_statut_n_est_pas_un_refus_de_cle():
    assert error_taxonomy.credential_rejected_in_chain(RuntimeError("network")) is False


# --- l'enveloppe ------------------------------------------------------------------

@pytest.fixture
def coffre(monkeypatch):
    rec = {"meta": [], "effacements": []}
    health._SANS_MARQUE.clear()

    def _update_meta(et, eid, connector, account, patch, conn=None):
        rec["meta"].append(((et, eid, connector, account), patch))
        return True

    def _clear(et, eid, connector, account):
        rec["effacements"].append((et, eid, connector, account))
        return True

    monkeypatch.setattr(credentials_store, "update_meta", _update_meta)
    monkeypatch.setattr(credentials_store, "clear_call_health", _clear)
    yield rec
    health._SANS_MARQUE.clear()


async def _appel(trace: dict, call_next):
    tok = session_org.set_call_trace(trace)
    try:
        return await ErrorEnvelopeMiddleware().on_call_tool(object(), call_next)
    finally:
        session_org.reset_call_trace(tok)


def test_un_401_sur_une_cle_byo_la_marque_unauthorized_source_appel(coffre):
    async def _echoue(ctx):
        _outil_qui_cure(401)

    with pytest.raises(McpError):
        asyncio.run(_appel({"credential_row": BYO}, _echoue))
    assert len(coffre["meta"]) == 1
    ligne, patch = coffre["meta"][0]
    assert ligne == BYO
    assert patch["health_ko"] is True
    assert patch["health_verdict"] == credentials_store.UNAUTHORIZED_VERDICT
    assert patch["health_source"] == credentials_store.CALL_SOURCE
    assert patch["health_reason"]


@pytest.mark.parametrize("row", [PLATEFORME, TENANT])
def test_une_cle_plateforme_ou_tenant_n_est_jamais_peinte(coffre, row):
    async def _echoue(ctx):
        raise _Amont(401)

    with pytest.raises(McpError):
        asyncio.run(_appel({"credential_row": row}, _echoue))
    assert coffre["meta"] == [] and coffre["effacements"] == []


@pytest.mark.parametrize("status", [403, 500])
def test_un_403_seul_ou_une_5xx_ne_marquent_pas(coffre, status):
    async def _echoue(ctx):
        raise _Amont(status)

    with pytest.raises(McpError):
        asyncio.run(_appel({"credential_row": BYO}, _echoue))
    assert coffre["meta"] == []


def test_un_succes_apres_le_refus_leve_la_marque_une_seule_fois(coffre):
    async def _echoue(ctx):
        raise _Amont(401)

    async def _reussit(ctx):
        return "ok"

    with pytest.raises(McpError):
        asyncio.run(_appel({"credential_row": BYO}, _echoue))
    assert asyncio.run(_appel({"credential_row": BYO}, _reussit)) == "ok"
    assert asyncio.run(_appel({"credential_row": BYO}, _reussit)) == "ok"
    assert coffre["effacements"] == [BYO]


# --- le démarquage sur un vrai PostgreSQL ------------------------------------------

@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_cle_refusee_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _ligne(connector: str, meta: dict) -> None:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO connector_credentials (entity_type, entity_id, "
                     "connector, account, secret_enc, meta) "
                     "VALUES ('org', '7', %s, '', 'x', %s::jsonb)",
                     (connector, json.dumps(meta)))


def _rouge(connector: str) -> bool:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        row = conn.execute("SELECT meta->>'health_ko' AS ko FROM connector_credentials "
                           "WHERE entity_type='org' AND entity_id='7' AND connector=%s",
                           (connector,)).fetchone()
    return row["ko"] == "true"


def test_un_succes_leve_les_marques_d_appel_et_garde_celle_de_la_sonde(live):
    _ligne("lemlist", {"health_ko": True, "health_reason": "HTTP 401",
                       "health_verdict": "unauthorized", "health_source": "call"})
    _ligne("theirstack", {"health_ko": True, "health_reason": "HTTP 402",
                          "health_verdict": "no_quota"})
    _ligne("linear", {"health_ko": True, "health_reason": "missing scope",
                      "health_verdict": "unauthorized"})
    assert credentials_store.clear_call_health("org", "7", "lemlist", "") is True
    assert credentials_store.clear_call_health("org", "7", "theirstack", "") is True
    assert credentials_store.clear_call_health("org", "7", "linear", "") is False
    assert not _rouge("lemlist") and not _rouge("theirstack")
    assert _rouge("linear"), "une marque de la sonde ne se lève qu'à la sonde"


def test_une_marque_de_sonde_efface_la_source_d_un_ancien_appel(live):
    """La sonde repasse sur une clé marquée par un appel : sa marque n'a plus de
    source d'appel, un succès d'appel ne la lève donc plus."""
    _ligne("folk", {"health_ko": True, "health_verdict": "unauthorized",
                    "health_source": "call"})
    health.record_health("folk", ("org", "7", ""), False, "missing scope",
                         "unauthorized")
    assert credentials_store.clear_call_health("org", "7", "folk", "") is False
    assert _rouge("folk")


# --- les connecteurs qui DÉCLARENT leur 403 ---------------------------------------
#
# Mesuré le 07/10/2026 : en 7 jours, 403 seuls ×35 sur serper et ×6 sur snitcher, que la
# règle générique (un 403 seul ne marque pas) laissait au vert. Chacun déclare son 403
# là où, et seulement là où, il ne peut vouloir dire que « clé refusée ».

def _snitcher_workspace():
    from unittest.mock import patch

    from fastmcp import FastMCP
    from oto_mcp.tools import snitcher

    patcher = patch("oto.tools.snitcher.client.SnitcherClient")
    cls = patcher.start()
    m = FastMCP("t")
    snitcher.register(m)
    return asyncio.run(m.get_tool("snitcher_workspace")).fn, cls, patcher


@pytest.fixture
def _cle_servie(monkeypatch):
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("k", False))


@pytest.mark.parametrize("op, methode", [("list", "list_workspaces"), ("me", "get_me")])
def test_snitcher_403_sur_le_compte_du_jeton_est_un_refus_de_cle(_cle_servie, op, methode):
    """`/me` et la liste des workspaces lisent le compte du JETON : un 403 « Access
    denied » n'y vise aucune ressource, c'est le jeton refusé (signaux #1145, #1345)."""
    from oto.tools.common.errors import UpstreamHTTPError

    fn, cls, patcher = _snitcher_workspace()
    try:
        getattr(cls.return_value, methode).side_effect = UpstreamHTTPError(
            403, "{'success': False, 'message': 'Access denied'}", service="snitcher")
        with pytest.raises(McpError) as exc:
            fn(op=op)
    finally:
        patcher.stop()
    assert error_taxonomy.credential_rejected_in_chain(exc.value) is True


def test_snitcher_403_sur_une_ressource_ne_marque_pas(_cle_servie):
    """Sur un workspace donné, Snitcher documente le 403 comme « insufficient
    permissions » : une ressource hors de portée, pas forcément un jeton mort."""
    from oto.tools.common.errors import UpstreamHTTPError

    fn, cls, patcher = _snitcher_workspace()
    try:
        cls.return_value.list_segments.side_effect = UpstreamHTTPError(
            403, "{'success': False, 'message': 'Access denied'}", service="snitcher")
        with pytest.raises(McpError) as exc:
            fn(op="segments", workspace_uuid="ws_1")
    finally:
        patcher.stop()
    assert error_taxonomy.credential_rejected_in_chain(exc.value) is False


@pytest.mark.parametrize("message, refusee", [
    ("Serper search 403: Unauthorized.", True),
    ("Serper scrape 403: Unauthorized", True),
    ("Serper search 403: Forbidden", False),      # un autre 403 : pas la signature
    ("Serper search 500: Internal error", False),
])
def test_serper_declare_son_403_unauthorized_et_lui_seul(message, refusee):
    from oto_mcp.tools import serper

    trouve = serper.cle_refusee(RuntimeError(message))
    assert (trouve is not None) is refusee
    if refusee:
        # Toujours une RuntimeError au même message : les appelants qui laissent
        # remonter un problème de clé le font comme avant.
        assert isinstance(trouve, RuntimeError) and str(trouve) == message
        assert error_taxonomy.credential_rejected_in_chain(trouve) is True
