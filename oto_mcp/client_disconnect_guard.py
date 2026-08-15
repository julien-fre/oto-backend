"""Garde ASGI : une déconnexion client sur `/mcp` ne doit pas empoisonner le pool.

**Le problème mesuré** (oto-backend#352, prod nuit du 15-16/08 : ~70 × 502 en 40 min,
durées 0,2 s — pas des timeouts). Un POST `/mcp` est en vol sur une session
streamable-http quand la session SE TERMINE (DELETE du client, ou fermeture du stream
côté client). Le SDK MCP pousse alors dans un stream mort (`writer.send` →
`anyio.BrokenResourceError`, `mcp/server/streamable_http.py`), et le POST se conclut
d'une des deux façons suivantes — les deux sont des **réponses ASGI incomplètes** :

- l'exception s'échappe de l'app ASGI. uvicorn journalise « Exception in ASGI
  application » puis, si la réponse était déjà partie, **ferme brutalement le
  transport** (`transport.close()`) ;
- l'app RETOURNE après avoir commencé la réponse sans la terminer (branche SSE : le
  task group annule la tâche de réponse quand `writer.send` lève, le `except Exception`
  du SDK avale, `_handle_post_request` retourne). uvicorn journalise « ASGI callable
  returned without completing response » et **ferme brutalement le transport**.

Dans les deux cas la fermeture est le vrai dégât : Caddy tenait cette connexion pour
**réutilisable dans son pool keep-alive**. Elle meurt sous lui → `reverseproxy.statusError`
→ 502, **y compris sur les requêtes REST voisines** qui héritent de la connexion
empoisonnée (vécu : 16 × 502 sur des claim/extend/thread_append de workers, 4-5 runs
tués). Le 502 ne frappe donc PAS que le client parti : il frappe les voisins.

**Ce que fait la garde** : le client est parti, plus personne ne lit — mais uvicorn et
Caddy, eux, ont besoin de voir une réponse COMPLÈTE pour garder la connexion saine. On
complète donc à sa place, et on ne laisse pas l'exception s'échapper :

- `http.response.start` pas encore parti → 202 (`Accepted`) corps vide ;
- déjà parti, corps non terminé → un dernier `http.response.body` vide (`more_body=False`) ;
- réponse déjà complète → rien à envoyer, on avale seulement l'exception.

**Périmètre volontairement étroit** — c'est la condition pour qu'une garde qui avale
une exception soit acceptable :

- **seulement POST sur le endpoint MCP** (la mécanique mesurée). Les autres méthodes et
  toute la face REST `/api/*` sont en pass-through intégral ;
- **seulement la classe « déconnexion client »** (`error_taxonomy._is_client_disconnect`
  — même prédicat que le drop Sentry, une seule source). **Toute autre exception
  TRAVERSE**, intacte, avec son traceback : un bug backend continue de faire un 500 et
  un event Sentry. Un test le fige (`tests/test_client_disconnect_guard.py`).

**Middleware ASGI EXTERNE** : posé autour de l'app racine, il ne touche pas la chaîne de
middlewares FastMCP (dont l'ordre est un contrat, cf. `tests/test_middleware_order.py`)
ni la lib `mcp` (non modifiée). Il est réversible d'une ligne dans `server.main`.
"""
from __future__ import annotations

import logging

from .error_taxonomy import _is_client_disconnect

logger = logging.getLogger(__name__)

# Le endpoint MCP (`FastMCP.http_app()` le monte sur `/mcp`), servi tel quel sur le host
# canonique comme sur les sous-domaines de projet (ADR 0032).
_MCP_PATH = "/mcp"


def _is_guarded(scope) -> bool:
    """Vrai pour un POST sur le endpoint MCP — le seul chemin où la race est mesurée.

    Élargir (DELETE, GET/SSE) serait un ACTE de conception, pas un détail : chaque
    méthode couverte est une exception de plus qu'on accepte d'avaler. On s'en tient à
    ce que #352 a mesuré.

    Égalité EXACTE sur le chemin, pas un `endswith` : `/.well-known/oauth-protected-
    resource/mcp` se termine aussi par `/mcp`. Le POST le tient hors de portée
    aujourd'hui, mais une garde ne doit pas dépendre d'une coïncidence."""
    if scope.get("type") != "http" or scope.get("method") != "POST":
        return False
    return (scope.get("path") or "").rstrip("/") == _MCP_PATH


def _is_disconnect(exc: BaseException) -> bool:
    """`_is_client_disconnect` + descente dans les groupes d'exceptions.

    anyio 4 remonte les erreurs d'un task group dans un `ExceptionGroup` — et le SDK MCP
    lance sa réponse SSE dans un task group. `_is_client_disconnect` remonte la chaîne
    `__cause__`/`__context__` mais ne descend pas dans `.exceptions` ; on le fait ici,
    localement, plutôt que d'élargir la taxonomie partagée (elle sert aussi à composer
    les erreurs rendues à l'agent — pas le même contrat).

    Duck-typing sur `.exceptions` : pas d'import d'`anyio`, et `BaseExceptionGroup`
    n'est builtin qu'à partir de 3.11 (le projet cible >=3.10)."""
    if _is_client_disconnect(exc):
        return True
    membres = getattr(exc, "exceptions", None)
    if isinstance(membres, (tuple, list)) and membres:
        return all(_is_disconnect(m) for m in membres)
    return False


class ClientDisconnectGuard:
    """Enveloppe l'app ASGI racine et rend au serveur une réponse toujours complète
    sur un POST `/mcp` dont le client est parti. Cf. docstring du module."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if not _is_guarded(scope):
            await self.app(scope, receive, send)
            return

        # `started` : les en-têtes sont partis (on ne peut plus changer le statut).
        # `finished` : le dernier morceau de corps est parti (réponse complète).
        etat = {"started": False, "finished": False}

        async def _send(message):
            kind = message.get("type")
            if kind == "http.response.start":
                etat["started"] = True
            elif kind == "http.response.body" and not message.get("more_body", False):
                etat["finished"] = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except BaseException as exc:                      # noqa: BLE001 — re-levé si ce n'en est pas une
            if not _is_disconnect(exc):
                raise                                     # un VRAI bug : il traverse, intact
            logger.debug("POST %s : client parti (%s) — réponse complétée côté serveur",
                         scope.get("path"), type(exc).__name__)
            await self._complete(etat, send)
            return
        # Retour NORMAL mais réponse laissée en plan (branche SSE annulée) : même dégât
        # sur le pool, même remède. Une réponse jamais COMMENCÉE n'est pas notre affaire
        # — uvicorn en fait un 500, qui est une réponse complète et un signal juste.
        if etat["started"] and not etat["finished"]:
            logger.debug("POST %s : réponse laissée incomplète — terminée côté serveur",
                         scope.get("path"))
            await self._complete(etat, send)

    @staticmethod
    async def _complete(etat, send) -> None:
        """Émet le strict nécessaire pour que la réponse soit complète aux yeux
        d'uvicorn. Les envois sont eux-mêmes protégés : la connexion peut déjà être
        morte, et une garde qui lève en se refermant ne servirait à rien."""
        if etat["finished"]:
            return
        try:
            if not etat["started"]:
                await send({"type": "http.response.start", "status": 202,
                            "headers": [(b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception as e:                            # noqa: BLE001
            logger.debug("clôture de réponse impossible (connexion déjà morte) : %s", e)
