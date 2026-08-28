"""`ErrorEnvelopeMiddleware` — le contrat d'erreur uniforme rendu à l'agent."""
from __future__ import annotations

from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from starlette.concurrency import run_in_threadpool

from .. import error_taxonomy
from ..auth.hooks import current_user_sub_from_token


def _reachable_suffix(connector: str) -> str:
    """Suffixe « des clés existent à portée » pour l'enveloppe d'erreur. Sync (DB) —
    à appeler via `run_in_threadpool`. Réutilise le seam d'`access` (aucune règle
    d'accès recopiée ici) ; chaîne vide si rien à portée ou hors contexte."""
    from .. import access
    sub = current_user_sub_from_token()
    if not sub:
        return ""
    return access._reachable_hint(sub, access.current_org(sub), connector)


class ErrorEnvelopeMiddleware(Middleware):
    """Contrat d'erreur uniforme rendu à l'agent (D2, oto-backend#124).

    Toute exception d'un tool est réécrite en `McpError` **scrubbée** (pas de
    stacktrace / route interne / id technique) portant `data.oto = {code, retryable,
    hint}` — l'agent peut alors DÉCIDER (retry / abandon / corriger l'input) au lieu
    de deviner sur un message brut. Les tools qui lèvent déjà une `McpError` curée
    voient leur message conservé (cf. `error_taxonomy.classify`).

    **Outermost** (ajouté AVANT `SentryToolErrorMiddleware`) : la chaîne s'exécute de
    l'extérieur vers l'intérieur, donc Sentry (plus interne) attrape l'exception
    d'ORIGINE en premier (vrai traceback capturé), la re-raise, et cette enveloppe la
    normalise EN DERNIER avant qu'elle ne quitte le serveur. Placer l'enveloppe plus
    interne masquerait le vrai traceback à Sentry.
    """

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except Exception as e:
            info = error_taxonomy.classify(e)
            data = {"code": info.code, "retryable": info.retryable}
            hint = info.hint
            # Outil non monté = le PREMIER mur. Sans ça l'agent installe le
            # connecteur, rappelle, et se prend un SECOND mur (« aucun credential »)
            # qui seul portait l'info utile — alors qu'une clé existe peut-être à
            # portée (équipe dont il est membre, autre org). On remonte les deux
            # d'un coup. DB SYNC → threadpool obligatoire (serveur mono-loop), et
            # best-effort : un hoquet ici ne doit jamais masquer l'erreur d'origine.
            if info.connector:
                try:
                    hint = (hint or "") + await run_in_threadpool(
                        _reachable_suffix, info.connector)
                # noqa: SILENT — dette déclarée : le hint « une clé existe à portée » disparaît (#424, verdict C)
                except Exception:  # noqa: BLE001
                    pass
            if hint:
                data["hint"] = hint
            raise McpError(ErrorData(
                code=error_taxonomy.jsonrpc_code(info),
                message=info.message,
                data={"oto": data},
            )) from e
