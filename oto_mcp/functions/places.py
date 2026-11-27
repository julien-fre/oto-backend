"""Les places du bac à sable : combien d'exécutions tournent EN MÊME TEMPS (03/11/2026).

Chaque exécution est bornée (`isolement` : 1 Go, un cœur, 64 tâches, sa durée) ; rien ne
bornait leur NOMBRE. Tout compte qui lit une fonction peut la lancer, tout membre peut
éprouver du code : N exécutions simultanées pesaient N × 1 Go sur la box. Ce module tient
le compte des exécutions en cours, et en refuse une de trop :

- au plus `OTO_FUNCTIONS_MAX_SIMULTANEES` (défaut `MAX_DEFAUT`) dans le processus ;
- au plus `PAR_COMPTE` par compte (`sub`) — un compte ne prend pas toutes les places.

**Au-delà : refus IMMÉDIAT**, `BacASableOccupe` (429 `sandbox_busy` côté capacité, avec
`Retry-After`), jamais une attente : une requête qui attend une place tient sa
connexion, et la file deviendrait la nouvelle charge.

**Le `Retry-After` dit quand une place se libère au plus tard** (25/11/2026). Chaque
place connaît le début de son exécution et la borne au-delà de laquelle elle est rendue
quoi qu'il arrive (`duree_max_s` : le délai de l'exécution plus ses grâces et l'arrêt
du processus). Refus par compte : ce qui reste à l'exécution de ce compte ; refus du
plafond : ce qui reste à la PREMIÈRE des exécutions en cours à devoir finir. Un
`ATTENTE_S` fixe faisait relancer toutes les 10 s une exécution qui en avait pour
plusieurs minutes, jusqu'à épuiser les relances de l'appelant. Le message dit aussi
lequel des deux plafonds a refusé. Une place sans borne connue, ou qui a dépassé la
sienne (journalisée : ce serait une place qui ne se rend pas), retombe sur `ATTENTE_S`.

**Le point commun** : l'aller simple (`executor.executer`) et le pont des recettes-code
(`pont.executer_avec_pont`) prennent leur place avant de lancer le processus et la
rendent après qu'il est mort (`finally` : exception, délai, annulation compris).

**Un `threading.Lock`, pas un `asyncio.Semaphore`** : l'aller simple joue dans un fil
(`run_in_threadpool`), le pont dans la boucle. Un sémaphore asyncio n'est ni sûr entre
fils ni utilisable hors de sa boucle ; un verrou de fil l'est des deux côtés. Il n'est
jamais tenu au-delà d'un compte (prendre ou rendre une place, sans attente ni E/S) : la
boucle ne l'attend pas.

⚠️ **Le plafond est PAR PROCESSUS.** Pendant une bascule bleu/vert, deux couleurs
tournent : le plafond effectif de la box double le temps de la bascule.
"""
from __future__ import annotations

import contextlib
import functools
import itertools
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

ENV_MAX = "OTO_FUNCTIONS_MAX_SIMULTANEES"
#: 4 × `isolement.MEMOIRE_MO` (1 Go) = 4 Go au pire, sous les ~6 Go que la box laisse
#: libres au service : il reste de quoi servir le reste pendant que quatre exécutions
#: tiennent leur plafond mémoire.
MAX_DEFAUT = 4
#: Les exécutions simultanées d'UN compte.
PAR_COMPTE = 1
#: Ce que le refus conseille d'attendre quand aucune place tenue n'a de borne connue
#: (ou qu'elle l'a dépassée) : sinon, `Retry-After` dit quand une place se libère.
ATTENTE_S = 10
#: Ce qu'une exécution échue laisse, au-delà de son délai, pour tuer son processus et
#: fermer ses tubes avant de rendre sa place : la borne de `duree_max_s` l'inclut.
MARGE_ARRET_S = 5.0


class ReglageInvalide(RuntimeError):
    """`OTO_FUNCTIONS_MAX_SIMULTANEES` illisible : levé au démarrage."""


class BacASableOccupe(RuntimeError):
    """Plus de place dans le bac à sable (le processus, ou ce compte) : rien n'est lancé."""

    def __init__(self, message: str, retry_after: int = ATTENTE_S):
        super().__init__(message)
        self.retry_after = retry_after


def _lire() -> int:
    brut = os.environ.get("OTO_FUNCTIONS_MAX_SIMULTANEES")
    if brut is None or brut.strip() == "":
        return MAX_DEFAUT
    try:
        valeur = int(brut.strip())
    except ValueError:
        valeur = 0
    if valeur < 1:
        raise ReglageInvalide(f"{ENV_MAX}={brut!r} illisible : un entier, au moins 1.")
    return valeur


@functools.cache
def plafond() -> int:
    """Le plafond du processus, lu une fois. Les bancs repartent par `cache_clear()`."""
    return _lire()


def verifier_configuration() -> None:
    """Au démarrage (`server.main`) : un réglage illisible refuse de démarrer ICI."""
    plafond()


@dataclass(frozen=True)
class _Tenue:
    """Une place tenue : par qui, depuis quand, jusqu'à quand au plus tard."""
    sub: Optional[str]
    debut: float
    #: L'instant (`time.monotonic`) où la place est rendue au plus tard ; None = inconnu.
    echeance: Optional[float]


_verrou = threading.Lock()
_par_compte: dict[Optional[str], int] = {}
_tenues: dict[int, _Tenue] = {}
_jetons = itertools.count(1)
_total = 0


def en_cours() -> int:
    """Les exécutions qui tiennent une place."""
    return _total


def _attente(tenues: list[_Tenue], maintenant: float) -> tuple[int, list[_Tenue]]:
    """`(les secondes avant que l'une de ces places se libère au plus tard, celles qui
    ont dépassé leur borne)`. Sous `_verrou`, sans E/S : l'appelant journalise après.
    Une place sans borne, ou qui a dépassé la sienne, peut se libérer à tout instant ou
    ne dit plus rien : l'attente ne dépasse alors pas `ATTENTE_S`."""
    vivantes = [t for t in tenues if t.echeance is not None and t.echeance > maintenant]
    depassees = [t for t in tenues if t.echeance is not None and t.echeance <= maintenant]
    if not vivantes:
        return ATTENTE_S, depassees
    attente = max(1, math.ceil(min(t.echeance for t in vivantes) - maintenant))
    return (min(attente, ATTENTE_S) if len(vivantes) < len(tenues) else attente), depassees


def _signaler(depassees: list[_Tenue], maintenant: float) -> None:
    """Ni le compte ni les données : seulement ce qui dit qu'une place ne se rend pas."""
    for t in depassees:
        logger.warning("bac à sable : une place est tenue %.0f s après sa borne (prise "
                       "il y a %.0f s)", maintenant - t.echeance, maintenant - t.debut)


def _prendre(sub: Optional[str], duree_max_s: Optional[float] = None) -> int:
    global _total
    maximum = plafond()
    refus: Optional[BacASableOccupe] = None
    with _verrou:
        maintenant = time.monotonic()
        if _par_compte.get(sub, 0) >= PAR_COMPTE:
            siennes = [t for t in _tenues.values() if t.sub == sub]
            attente, depassees = _attente(siennes, maintenant)
            depuis = (f" (started {max(0, int(maintenant - siennes[0].debut))} s ago)"
                      if siennes else "")
            refus = BacASableOccupe(
                f"sandbox_busy: this account already has {PAR_COMPTE} execution running "
                f"in the sandbox{depuis} — the per-account limit, whatever the server's "
                f"load; retry in {attente} s, once it has finished.", retry_after=attente)
        elif _total >= maximum:
            attente, depassees = _attente(list(_tenues.values()), maintenant)
            refus = BacASableOccupe(
                f"sandbox_busy: the sandbox is running its maximum of {maximum} "
                "simultaneous executions — the server-wide limit, shared by all "
                f"accounts; retry in {attente} s, when the first of them is due to end.",
                retry_after=attente)
        else:
            _par_compte[sub] = _par_compte.get(sub, 0) + 1
            _total += 1
            jeton = next(_jetons)
            _tenues[jeton] = _Tenue(sub, maintenant, None if duree_max_s is None
                                    else maintenant + duree_max_s)
            return jeton
    _signaler(depassees, maintenant)
    raise refus


def _rendre(sub: Optional[str], jeton: Optional[int] = None) -> None:
    global _total
    with _verrou:
        reste = _par_compte.get(sub, 0) - 1
        if reste > 0:
            _par_compte[sub] = reste
        else:
            _par_compte.pop(sub, None)
        _total -= 1
        _tenues.pop(jeton, None)


@contextlib.contextmanager
def place(sub: Optional[str], duree_max_s: Optional[float] = None) -> Iterator[None]:
    """Une place pour UNE exécution de `sub`, rendue en sortant quoi qu'il arrive. Pas
    de place : `BacASableOccupe`, tout de suite, et rien n'est pris. `duree_max_s` : le
    temps au bout duquel la place est rendue au plus tard (délai, grâces, arrêt du
    processus compris) — ce que le `Retry-After` d'un refus annonce."""
    jeton = _prendre(sub, duree_max_s)
    try:
        yield
    finally:
        _rendre(sub, jeton)
