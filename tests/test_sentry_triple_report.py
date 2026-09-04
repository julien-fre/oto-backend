"""oto-backend#869 — chaque erreur reportée l'était TROIS fois.

`SentryToolErrorMiddleware` est le SEUL capteur qui étiquette (`mcp.tool`, l'utilisateur) ;
les deux autres copies n'ajoutaient rien :
1. `sentry_sdk.integrations.mcp.MCPIntegration`, auto-activée par le SDK dès
   `mcp>=1.15.0` — capture la même `McpError`, sans étiquette, sans passer par
   `_before_send` (d'où l'issue Sentry au titre trompeur « Erreur interne du serveur »).
2. La `LoggingIntegration` du SDK relayait `logger.exception(...)` de
   `fastmcp.server.server` — même événement que le middleware, en double.

Les deux coupes sont posées à l'init (`disabled_integrations`, `ignore_logger`) :
zéro perte d'information, le middleware reste le seul chemin.
"""
from __future__ import annotations

from sentry_sdk.integrations.mcp import MCPIntegration

from oto_mcp import sentry_setup


def test_mcp_integration_est_desactivee_a_linit(monkeypatch):
    vus: dict = {}
    monkeypatch.setattr(sentry_setup.sentry_sdk, "init", lambda **kw: vus.update(kw))
    monkeypatch.setattr(sentry_setup, "ignore_logger", lambda name: None)
    monkeypatch.setenv("OTO_SENTRY_DSN", "https://x@example.invalid/1")

    assert sentry_setup.init_sentry() is True

    disabled = vus.get("disabled_integrations")
    assert disabled and any(isinstance(i, MCPIntegration) for i in disabled), (
        "MCPIntegration doit être dans `disabled_integrations` — sinon le SDK la "
        "réactive automatiquement (mcp installé >= 1.15.0) et le triplet revient.")


def test_le_logger_fastmcp_est_mis_en_sourdine_cote_sentry(monkeypatch):
    vus: list = []
    monkeypatch.setattr(sentry_setup.sentry_sdk, "init", lambda **kw: None)
    monkeypatch.setattr(sentry_setup, "ignore_logger", lambda name: vus.append(name))
    monkeypatch.setenv("OTO_SENTRY_DSN", "https://x@example.invalid/1")

    assert sentry_setup.init_sentry() is True

    assert vus == ["fastmcp.server.server"], (
        "sans ce filtre, `logger.exception` de fastmcp double l'événement du "
        "middleware — le seul étiqueté (mcp.tool, utilisateur).")
