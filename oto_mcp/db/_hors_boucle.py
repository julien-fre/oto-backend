"""Garde d'EXÉCUTION : un accès base fait depuis le thread de la boucle d'événements.

Le serveur est mono-loop (`docs/event-loop-perf.md`). `execute()` ne met en thread que les
handlers de capacité SYNCHRONES : le corps d'un `async def` tourne SUR la boucle, et tout
SQL synchrone qu'il appelle — directement OU par une chaîne d'assistants — tient le serveur
entier le temps de la requête (gel de prod du 21/09/2026 : `me.agent_context`, ~140 s). Les
garde-fous d'avant ne voyaient que l'`async def` SANS `await`, ou observaient après coup
(`loop_watch`) : aucun ne nommait le fautif AVANT qu'il gèle.

Le passage obligé de toute requête est `_conn._connect()` (le pool n'est ouvert nulle part
ailleurs). Y poser la question — « y a-t-il une boucle qui tourne dans CE thread ? » —
attrape les chemins indirects sans rien savoir du code appelant : un thread du threadpool
n'a pas de boucle (`get_running_loop` lève), un `async def` en a une.

- **Production** : un `logger.warning` par SITE (une fois par process), avec la pile
  compacte. Jamais une exception : un site en défaut est une lenteur, pas une panne, et la
  lever le transformerait en panne.
- **Tests** : `HorsBoucle` est LEVÉE pour tout site absent du stock gelé
  (`tests/_stock_db_hors_boucle.py`, posé par `configurer`). Le stock ne fait que rétrécir :
  `tests/test_db_hors_boucle.py` échoue aussi quand un site listé n'est plus fautif.

Le SITE est la fonction `async def` de `oto_mcp` la plus interne dans la pile : c'est celle
qu'il faut décharger (`await run_in_threadpool(...)`), quel que soit l'assistant synchrone
qui touche la base. `module::qualname`, le même nom que rend le balayage statique
(`tests/_appels_db_hors_boucle.py`).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_RACINE = str(Path(__file__).resolve().parent.parent) + "/"     # …/oto_mcp/
_DB = _RACINE + "db/"
_CO_COROUTINE = inspect.CO_COROUTINE
_PROFONDEUR_PILE = 12


class HorsBoucle(RuntimeError):
    """Accès base depuis la boucle, hors du stock gelé — levée en test seulement."""


_strict = False
_tolere: frozenset[str] = frozenset()
_deja_vus: set[str] = set()


def configurer(*, strict: bool, tolere=()) -> None:
    """Posé par `tests/conftest.py`. Sans appel : mode production (avertir, jamais lever)."""
    global _strict, _tolere
    _strict = strict
    _tolere = frozenset(tolere)
    _deja_vus.clear()


def _site(frame) -> tuple[str, list[str]] | None:
    """(clé du site, pile compacte) — ou `None` quand aucune coroutine `oto_mcp` n'est
    dans la pile : un test qui pilote lui-même un assistant synchrone depuis sa propre
    coroutine n'est pas un chemin de production (et un `call_soon` qui touche la base,
    sans coroutine, reste à voir par `loop_watch`)."""
    coroutine = None
    pile: list[str] = []
    f = frame
    while f is not None:
        code = f.f_code
        chemin = code.co_filename
        if chemin.startswith(_RACINE):
            cle = f"{f.f_globals.get('__name__', '?')}::{code.co_qualname}"
            if len(pile) < _PROFONDEUR_PILE:
                pile.append(f"{cle}:{f.f_lineno}")
            if (code.co_flags & _CO_COROUTINE and coroutine is None
                    and not chemin.startswith(_DB)):
                coroutine = cle
        f = f.f_back
    return (coroutine, pile) if coroutine else None


def verifier() -> None:
    """À appeler en tête de `_connect()` / `_connect_autocommit()`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return                         # thread sans boucle : threadpool, démarrage, timer
    trouve = _site(sys._getframe(1))   # la pile ENTIÈRE est parcourue : le point de départ importe peu
    if trouve is None:
        return
    site, pile = trouve
    if site in _tolere:
        return
    if _strict:
        raise HorsBoucle(
            f"accès base depuis la boucle d'événements, dans `{site}` — le serveur est "
            "mono-loop : ce SQL synchrone gèle TOUT le monde le temps de la requête. "
            "Décharge-le : `await run_in_threadpool(...)` (ou rends le handler `def` "
            "synchrone). Cf. docs/event-loop-perf.md. Pile : " + " <- ".join(pile))
    if site not in _deja_vus:
        _deja_vus.add(site)
        logger.warning(
            "db.hors_boucle site=%s — accès base SYNCHRONE dans la boucle (le serveur "
            "entier attend la requête) ; pile : %s", site, " <- ".join(pile))
