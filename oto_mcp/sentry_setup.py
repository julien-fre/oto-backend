"""Error tracking Sentry (SaaS) — init + capture des exceptions de tools MCP.

Deux surfaces d'erreur, deux mécanismes :
- **Routes REST `/api/*`** : l'intégration Starlette du SDK (auto-activée par
  `sentry-sdk[starlette]`) capture les 500 ASGI avec traceback complet. Rien à
  câbler ici — il suffit que `init_sentry()` tourne AVANT `mcp.http_app()`.
- **Tools MCP** : une exception de tool devient une erreur JSON-RPC en HTTP 200 →
  l'intégration Starlette ne la voit pas. `SentryToolErrorMiddleware` la capture là
  où l'exception est vivante (vrai traceback), puis re-raise (comportement inchangé).

Gaté par `OTO_SENTRY_DSN` : absent → `sentry_sdk` n'est jamais initialisé, tout
`capture_exception` est un no-op. Le serveur boote normalement sans Sentry.

RGPD : `send_default_pii=False` (pas d'IP/cookies/headers auto-collectés) et on
n'attache JAMAIS les arguments d'appel (emails, données entreprise) à l'event —
seulement le nom du tool + le `sub` Logto (id opaque pseudonyme, utile au debug).

Sentry = défauts du CODE. Un **refus client amont** (4xx d'une API tierce : input
rejeté, credential invalide, cible absente) n'en est pas un — c'est une *erreur de
connecteur gérée*, déjà tracée dans le backlog `tool_calls` (calllog) et renvoyée à
l'agent en `ToolError`. On la classe **par type** (`UpstreamHTTPError` d'oto-core,
ou `httpx`/`requests` HTTPError, ou erreur connecteur typée portant un statut) et on
ne la reporte pas. Deux chemins de capture à neutraliser : le middleware explicite
(qui ne capture pas) et la LoggingIntegration sur le `logger.error` de fastmcp (le
`before_send` la droppe). Les 5xx et les vraies exceptions code restent reportées.
"""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Optional

import sentry_sdk
from fastmcp.server.middleware import Middleware
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations.mcp import MCPIntegration

from .auth.hooks import current_client_id_from_token, current_user_sub_from_token
# Classifieurs partagés (D2, #124) : source unique de la taxonomie d'exceptions,
# consommée aussi par `ErrorEnvelopeMiddleware`. Ré-exportés ici (les tests et le
# reste du module les référencent via `sentry_setup`).
from .error_taxonomy import (  # noqa: F401
    _USER_INPUT_CODES,
    _is_arg_validation_error,
    _is_client_disconnect,
    _is_expected_error,
    _is_managed_connector_error,
    _is_user_input_error,
    _upstream_status,
)

logger = logging.getLogger("oto_mcp")

# Event Sentry capturé pour l'appel EN COURS (extension OTO-LOCALE) : posé par
# `SentryToolErrorMiddleware` (innermost), relu par le sink calllog (plus externe,
# même tâche → la ContextVar mutée ici lui est visible) qui le stampe sur la ligne
# `tool_calls`. Ferme le détour « erreur au journal → chercher à la main dans Sentry
# par user.id » : la ligne d'audit porte le lien vers le traceback.
_LAST_EVENT_ID: ContextVar[Optional[str]] = ContextVar("oto_sentry_event_id", default=None)


def current_tool_event_id() -> Optional[str]:
    """Event id Sentry de l'appel courant, ou None (nominal / Sentry off)."""
    try:
        return _LAST_EVENT_ID.get()
    # noqa: SILENT — hors contexte de requête : pas d'event_id à corréler
    except Exception:
        return None


def _before_send(event, hint):
    """Droppe les erreurs gérées — couvre aussi la copie LoggingIntegration.

    Deux familles, deux raisons : l'erreur GÉRÉE (`_is_expected_error` : 4xx amont,
    refus d'entrée…) n'est pas un bug ; la DÉCONNEXION CLIENT n'est même pas une
    erreur — le client a raccroché pendant qu'on lui répondait. Cette seconde
    famille est traitée ici et pas dans `_is_expected_error` parce qu'elle ne
    concerne que Sentry : elle n'atteint aucun agent (cf. `_is_client_disconnect`).
    """
    exc_info = (hint or {}).get("exc_info")
    if exc_info and (_is_expected_error(exc_info[1]) or _is_client_disconnect(exc_info[1])):
        return None
    return event


def init_sentry() -> bool:
    """Initialise Sentry si `OTO_SENTRY_DSN` est posé. Retourne True si actif."""
    dsn = os.environ.get("OTO_SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry désactivé (OTO_SENTRY_DSN absent)")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("OTO_SENTRY_ENV", "production"),
        release=os.environ.get("OTO_SENTRY_RELEASE") or None,
        # RGPD : pas d'IP / cookies / headers auto-collectés.
        send_default_pii=False,
        # ⚠️ NE PAS RETIRER — le défaut du SDK est `True` (vérifié sentry-sdk 2.63.0),
        # et `send_default_pii=False` ne couvre PAS le contenu des frames : chaque
        # exception repartait avec les variables locales de toute la pile, dont
        # celles du chemin de résolution de credential, qui tiennent le secret
        # DÉCHIFFRÉ (#564). Le réglage, pas une liste à scruber : une liste redevient
        # fausse au premier renommage. Ce qu'on perd au diagnostic est mince — type,
        # message et pile restent. Cliquet : `tests/test_journal_no_plaintext_secret.py`.
        include_local_variables=False,
        # Tracing de perf désactivé par défaut (on cible l'error tracking).
        traces_sample_rate=float(os.environ.get("OTO_SENTRY_TRACES_SAMPLE_RATE", "0") or "0"),
        # Ne pas reporter les erreurs gérées (4xx amont + refus d'entrée/config
        # user McpError) : pas des bugs backend.
        before_send=_before_send,
        # oto-backend#869 — le SDK AUTO-ACTIVE `MCPIntegration` dès `mcp>=1.15.0`
        # (nous sommes en mcp 1.27.2) : elle capture la MÊME `McpError` que
        # `SentryToolErrorMiddleware` ci-dessous, SANS le tag `mcp.tool` ni
        # l'utilisateur, et sans passer par `_before_send` côté taxonomie — d'où
        # l'issue Sentry au titre trompeur « Erreur interne du serveur » et un
        # triplet d'événements par erreur (mesuré : 528/528/528, 293/293/293).
        # Coupée ici : zéro perte d'information, le middleware reste le SEUL
        # capteur, celui qui étiquette.
        disabled_integrations=[MCPIntegration()],
    )
    # oto-backend#869 — la 2ᵉ copie du triplet : la LoggingIntegration du SDK relaie
    # `logger.exception(f"Error calling tool {name!r}")` de fastmcp
    # (`fastmcp/server/server.py`, logger `fastmcp.server.server`) vers Sentry. Le
    # `before_send` la droppe déjà si l'erreur est gérée, mais sur une erreur RÉELLE
    # elle double `SentryToolErrorMiddleware` sans rien ajouter (pas de tag, pas
    # d'utilisateur) — l'événement du middleware suffit.
    ignore_logger("fastmcp.server.server")
    logger.info("Sentry actif (env=%s)", os.environ.get("OTO_SENTRY_ENV", "production"))
    return True


class SentryToolErrorMiddleware(Middleware):
    """Capture les exceptions des tools MCP vers Sentry, puis re-raise.

    No-op si Sentry n'est pas initialisé (`capture_exception` ne fait rien). Sur le
    chemin nominal, ce middleware ne fait que déléguer — aucun surcoût. Une **erreur
    gérée** (4xx amont OU refus d'entrée/config user) n'est PAS capturée (cf. module).
    """

    async def on_call_tool(self, context, call_next):
        # Remise à zéro par appel : sans elle, un event capturé plus tôt dans la même
        # tâche serait stampé sur la ligne d'un appel suivant, sain.
        _LAST_EVENT_ID.set(None)
        try:
            return await call_next(context)
        except Exception as e:
            if not _is_expected_error(e):
                try:
                    with sentry_sdk.new_scope() as scope:
                        scope.set_tag("mcp.tool", context.message.name)
                        try:
                            sub = current_user_sub_from_token()
                        # noqa: SILENT — sans sub, la trace reste anonyme plutôt que fausse
                        except Exception:
                            sub = None
                        if sub:
                            scope.set_user({"id": sub})
                        # Surface cliente (`azp` : claude.ai, Claude Code…) — où
                        # l'erreur se produit, pas qui l'a causée.
                        client = current_client_id_from_token()
                        if client:
                            scope.set_tag("mcp.client", client)
                        # L'id retourné (None si Sentry est off ou l'event droppé par
                        # `before_send`) devient le lien journal → traceback.
                        _LAST_EVENT_ID.set(sentry_sdk.capture_exception(e))
                # noqa: SILENT — la capture ne doit jamais masquer l'erreur d'origine
                except Exception:
                    # La capture ne doit jamais masquer l'erreur d'origine.
                    pass
            raise
