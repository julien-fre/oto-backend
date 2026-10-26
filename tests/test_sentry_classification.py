"""Politique de bruit Sentry : un refus client amont (4xx) n'est pas un bug backend.

Garde-fou de la classification PAR TYPE de `sentry_setup` (ADR : Sentry = défauts du
code ; les 4xx d'API tierces vivent dans le backlog `tool_calls`, pas dans Sentry).
"""
from oto_mcp.mcp_errors import McpError
from mcp.types import ErrorData, INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST

from oto_mcp.sentry_setup import (
    _before_send,
    _is_expected_error,
    _is_managed_connector_error,
    _is_user_input_error,
    _upstream_status,
)


class _Upstream(Exception):
    """Mime oto.tools.common.UpstreamHTTPError (porte .status_code)."""

    def __init__(self, status_code):
        self.status_code = status_code


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _HTTPStatusError(Exception):
    """Mime httpx.HTTPStatusError / requests.exceptions.HTTPError (.response)."""

    def __init__(self, status_code):
        self.response = _Resp(status_code)


class _NinjaError(RuntimeError):
    """Erreur connecteur typée maison (.status)."""

    def __init__(self, status):
        self.status = status


def _chained(top, cause):
    try:
        try:
            raise cause
        except Exception as c:
            raise top from c
    except Exception as e:
        return e


def test_upstream_status_extraction():
    assert _upstream_status(_Upstream(422)) == 422
    assert _upstream_status(_HTTPStatusError(404)) == 404
    assert _upstream_status(_NinjaError(400)) == 400
    assert _upstream_status(KeyError("x")) is None


def test_4xx_are_managed_connector_errors():
    assert _is_managed_connector_error(_Upstream(422))
    assert _is_managed_connector_error(_Upstream(401))
    assert _is_managed_connector_error(_HTTPStatusError(403))
    assert _is_managed_connector_error(_NinjaError(400))
    # fastmcp emballe dans un ToolError : on remonte la chaîne jusqu'à l'amont
    assert _is_managed_connector_error(
        _chained(Exception("Error calling tool 'folk_record'"), _Upstream(422))
    )


def test_real_oto_core_connector_errors_are_classified():
    # Seam oto-core (>=1.14.0) : UnipileError porte status_code aux sites de
    # raise HTTP ; ZohoAuthError (refus OAuth en HTTP 200) = 401 synthétique.
    from oto.tools.unipile.client import UnipileError
    from oto.tools.zoho import ZohoAuthError
    assert _is_managed_connector_error(UnipileError("Unipile 422: x", status_code=422))
    assert _is_managed_connector_error(ZohoAuthError("Zoho OAuth error: invalid_client"))
    assert not _is_managed_connector_error(UnipileError("Unipile 500: x", status_code=500))
    assert not _is_managed_connector_error(UnipileError("Unipile: erreur réseau (Timeout)."))


def test_real_bugs_and_5xx_still_reported():
    assert not _is_managed_connector_error(_Upstream(503))   # amont cassé, ops-worthy
    assert not _is_managed_connector_error(KeyError("sub"))  # vrai bug code
    # plus de match par string : un Exception nu sans statut n'est PAS classé géré
    assert not _is_managed_connector_error(Exception("HTTP 422: x"))


def test_mcp_input_errors_are_user_config_noise():
    # « pose ta clé », « connecte ton compte », param/org invalide → refus user
    assert _is_user_input_error(McpError(ErrorData(code=INVALID_PARAMS, message="Aucune clé `hunter`")))
    assert _is_user_input_error(McpError(ErrorData(code=INVALID_REQUEST, message="x")))
    # remonte la chaîne : ToolError fastmcp emballant la McpError de config
    assert _is_user_input_error(
        _chained(Exception("Error calling tool 'gmail_message'"),
                 McpError(ErrorData(code=INVALID_PARAMS, message="Aucun compte Google connecté")))
    )


def test_mcp_internal_and_real_bugs_still_reported():
    # INTERNAL_ERROR = vrai défaut serveur, pas un refus user
    assert not _is_user_input_error(McpError(ErrorData(code=INTERNAL_ERROR, message="boom")))
    # corruption coffre (clé périmée) = Runtimeable, on VEUT la voir
    assert not _is_expected_error(RuntimeError("credential indéchiffrable (clé périmée)"))
    assert not _is_expected_error(AttributeError("module 'oto_mcp.db' has no attribute 'has_option_comp'"))


def test_expected_error_unifies_both_classes():
    assert _is_expected_error(_Upstream(422))                                  # 4xx amont
    assert _is_expected_error(McpError(ErrorData(code=INVALID_PARAMS, message="x")))  # config user
    assert not _is_expected_error(_Upstream(503))
    assert not _is_expected_error(KeyError("sub"))


def test_before_send_drops_managed_errors():
    assert _before_send({}, {"exc_info": (_Upstream, _Upstream(422), None)}) is None
    e = McpError(ErrorData(code=INVALID_PARAMS, message="connecte ton compte"))
    assert _before_send({}, {"exc_info": (McpError, e, None)}) is None
    ev = {"k": "v"}
    assert _before_send(ev, {"exc_info": (_Upstream, _Upstream(500), None)}) is ev
    assert _before_send(ev, {"exc_info": (KeyError, KeyError("x"), None)}) is ev
    assert _before_send(ev, None) is ev  # event REST sans exc_info → conservé


def _validation_error():
    """Construit une vraie ValidationError pydantic (args rejetés)."""
    from pydantic import BaseModel, ValidationError

    class _M(BaseModel):
        x: int

    try:
        _M(x="pas un entier")
    except ValidationError as e:
        return e


def test_pydantic_validation_error_is_expected():
    # args rejetés (le LLM a passé de mauvais paramètres) = refus d'entrée, pas un bug
    ve = _validation_error()
    assert _is_expected_error(ve)
    assert _before_send({"k": "v"}, {"exc_info": (type(ve), ve, None)}) is None
    # chaînée (fastmcp emballe dans un ToolError) → toujours reconnue
    wrapped = RuntimeError("Error calling tool")
    wrapped.__cause__ = ve
    assert _is_expected_error(wrapped)
    # une vraie exception code reste reportée
    assert not _is_expected_error(KeyError("sub"))


def test_upstream_outage_is_a_grouped_warning():
    """2026-10-19/20 : Apollo / Serper / Google / Unipile qui toussent ouvraient
    chacun une issue « error » sur #bug-report. Panne amont = warning ; un
    timeout de notre code reste une erreur."""
    import requests
    import httpx
    from oto_mcp.sentry_setup import upstream_level

    assert upstream_level(_Upstream(500)) == "upstream_5xx"
    assert upstream_level(_HTTPStatusError(502)) == "upstream_5xx"
    assert upstream_level(_Upstream(504)) == "upstream_timeout"
    assert upstream_level(_chained(RuntimeError("Error calling tool"),
                                   requests.exceptions.ReadTimeout("read timeout=60"))) \
        == "upstream_timeout"
    assert upstream_level(_chained(RuntimeError("Unipile: network error (ReadTimeout)"),
                                   httpx.ReadTimeout("timed out"))) == "upstream_timeout"
    # Not an outage: 4xx, our own timeouts, plain bugs.
    assert upstream_level(_Upstream(422)) is None
    assert upstream_level(TimeoutError("statement timeout")) is None
    assert upstream_level(KeyError("x")) is None


def test_middleware_tags_upstream_outage_as_warning(monkeypatch, pannes_neuves):
    import asyncio

    import requests
    import sentry_sdk
    from oto_mcp import sentry_setup

    seen = {}

    def fake_capture(e):
        scope = sentry_sdk.get_current_scope()
        seen.update(level=scope._level, fingerprint=scope._fingerprint)
        return "evt"

    monkeypatch.setattr(sentry_setup.sentry_sdk, "capture_exception", fake_capture)
    monkeypatch.setattr(sentry_setup, "current_user_sub_from_token", lambda: None)
    monkeypatch.setattr(sentry_setup, "current_client_id_from_token", lambda: None)

    class _Msg:
        name = "apollo_search_people"

    class _Ctx:
        message = _Msg()

    async def boom(_ctx):
        raise requests.exceptions.ReadTimeout("read timeout=60")

    try:
        asyncio.run(sentry_setup.SentryToolErrorMiddleware().on_call_tool(_Ctx(), boom))
    except requests.exceptions.ReadTimeout:
        pass
    assert seen == {"level": "warning",
                    "fingerprint": ["upstream", "apollo_search_people", "upstream_timeout"]}


# --- Reprise : groupée mais pas muette, et jamais une erreur à nous -------------------

import pytest  # noqa: E402


@pytest.fixture()
def pannes_neuves(monkeypatch):
    """Compteur de pannes vide et horloge à la main, pour chaque test."""
    from oto_mcp import sentry_setup
    horloge = {"t": 1000.0}
    monkeypatch.setattr(sentry_setup, "_PANNES", {})
    monkeypatch.setattr(sentry_setup, "_ESCALADEES", {})
    monkeypatch.setattr(sentry_setup, "_horloge", lambda: horloge["t"])
    return horloge


def _appeler(monkeypatch, outil, exc):
    """Fait échouer `outil` par `exc` à travers le middleware ; rend ce que le scope
    portait au moment de la capture."""
    import asyncio

    import sentry_sdk
    from oto_mcp import sentry_setup

    vu = {}

    def fake_capture(e):
        scope = sentry_sdk.get_current_scope()
        vu.update(level=scope._level, fingerprint=scope._fingerprint,
                  tags=dict(scope._tags), extras=dict(scope._extras))
        return "evt"

    monkeypatch.setattr(sentry_setup.sentry_sdk, "capture_exception", fake_capture)
    monkeypatch.setattr(sentry_setup, "current_user_sub_from_token", lambda: None)
    monkeypatch.setattr(sentry_setup, "current_client_id_from_token", lambda: None)

    class _Msg:
        name = outil

    class _Ctx:
        message = _Msg()

    async def boom(_ctx):
        raise exc

    with pytest.raises(type(exc)):
        asyncio.run(sentry_setup.SentryToolErrorMiddleware().on_call_tool(_Ctx(), boom))
    return vu


def test_panne_groupee_garde_son_compte(monkeypatch, pannes_neuves):
    """Trois échecs de Serper : trois événements `warning`, une seule empreinte, et
    chacun dit combien d'échecs la panne compte déjà."""
    vus = [_appeler(monkeypatch, "web_read", _Upstream(500)) for _ in range(3)]
    assert {v["level"] for v in vus} == {"warning"}
    assert {tuple(v["fingerprint"]) for v in vus} == {("upstream", "web_read", "upstream_5xx")}
    assert [v["extras"]["upstream.failures_in_window"] for v in vus] == [1, 2, 3]
    assert {v["tags"]["upstream"] for v in vus} == {"upstream_5xx"}


def test_panne_qui_dure_remonte_en_erreur_avec_son_compte(monkeypatch, pannes_neuves, caplog):
    """Une panne qui dure n'est pas muette : au seuil, un événement `error` « panne
    durable » (son compte en extra) et une ligne au journal ; puis de nouveau des
    warnings, et une nouvelle escalade si elle dure encore une fenêtre plus tard."""
    import logging

    from oto_mcp import sentry_setup
    seuil = sentry_setup._SEUIL_PANNE_DURABLE
    caplog.set_level(logging.WARNING, logger="oto_mcp")
    vus = []
    for _ in range(seuil + 1):
        vus.append(_appeler(monkeypatch, "apollo_search_people", _Upstream(503)))
        pannes_neuves["t"] += 30
    assert [v["level"] for v in vus[:seuil - 1]] == ["warning"] * (seuil - 1)
    durable = vus[seuil - 1]
    assert durable["level"] == "error"
    assert durable["fingerprint"] == ["upstream-durable", "apollo_search_people", "upstream_5xx"]
    assert durable["tags"]["upstream.durable"] == "true"
    assert durable["extras"]["upstream.failures_in_window"] == seuil
    assert vus[seuil]["level"] == "warning", "une escalade par fenêtre, pas une par échec"
    lignes = [r.getMessage() for r in caplog.records if "panne amont durable" in r.getMessage()]
    assert lignes == [f"panne amont durable : apollo_search_people (upstream_5xx), "
                      f"{seuil} échecs en 15 min"]

    # La panne dure encore une fenêtre plus tard : elle escalade de nouveau.
    pannes_neuves["t"] += sentry_setup._FENETRE_PANNE_S
    for _ in range(seuil):
        dernier = _appeler(monkeypatch, "apollo_search_people", _Upstream(503))
        pannes_neuves["t"] += 30
    assert dernier["level"] == "error"


def test_des_echecs_espaces_ne_font_pas_une_panne_durable(monkeypatch, pannes_neuves):
    from oto_mcp import sentry_setup
    for _ in range(sentry_setup._SEUIL_PANNE_DURABLE + 2):
        vu = _appeler(monkeypatch, "gmail", _Upstream(502))
        pannes_neuves["t"] += sentry_setup._FENETRE_PANNE_S + 1
    assert vu["level"] == "warning" and vu["extras"]["upstream.failures_in_window"] == 1


def test_une_erreur_a_nous_n_est_pas_une_panne_amont(monkeypatch, pannes_neuves):
    """Un 5xx porté par NOS exceptions (stockage absent, chiffrement indisponible) et un
    défaut de code levé en traitant la réponse d'un tiers restent des erreurs, hors
    du groupe des pannes amont."""
    import requests

    from oto_mcp import sentry_setup
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.media_store import MediaError

    assert sentry_setup.upstream_level(MediaError(500, "unknown_prefix", "x")) is None
    assert sentry_setup.upstream_level(
        AuthzDenied(503, "encryption_unavailable", "x")) is None
    # notre bug dans le `except` qui traitait un 500 / un timeout de Serper
    assert sentry_setup.upstream_level(_chained(KeyError("organic"), _Upstream(500))) is None
    assert sentry_setup.upstream_level(
        _chained(AttributeError("json"), requests.exceptions.ReadTimeout("t"))) is None
    # mais une erreur à nous qui ENVELOPPE une vraie panne reste une panne
    assert sentry_setup.upstream_level(
        _chained(MediaError(502, "upstream", "x"), _Upstream(500))) == "upstream_5xx"

    vu = _appeler(monkeypatch, "web_read", _chained(KeyError("organic"), _Upstream(500)))
    assert vu["level"] is None and vu["fingerprint"] is None, "erreur normale, non groupée"
    assert "upstream" not in vu["tags"]
    assert sentry_setup._PANNES == {}, "notre bug ne compte pas comme un échec amont"
