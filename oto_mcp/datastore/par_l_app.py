"""L'accès à un tableau À TRAVERS UNE APP seulement (`docs/apps.md` § Accès par l'app).

Une personne peut tenir un tableau par l'app SEULE (`app_table_access`, posé par
`oto_apps op=grant_through_app`) : elle n'en a aucun partage, et aucune porte ordinaire
ne la laisse y toucher — ni les outils `data_*`, ni la face REST des tableaux, ni
l'écran Tableaux : aucune ne lit cette table.

Le seul endroit où cet accès compte : la PORTÉE qu'ouvre l'appel d'une app
(`capabilities/apps_appel._par_l_app`), le temps de cet appel, pour les seuls tableaux
du manifeste RESSERRÉ au rôle du lecteur. Dans cette portée, `DatastorePg._trouver`
demande `droit(ns_id)` quand la résolution ordinaire ne trouve rien — ou ne donne pas
l'écriture —, et sert le tableau. Tout le reste de l'appel (manifeste, champs, valeurs,
rôle, auteur ∩ lecteur, consentement) reste jugé comme avant : la portée ne fait que
remplacer « le lecteur a un partage du tableau ».

Hors de la portée (valeur par défaut `None`) : rien, à aucun endroit.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Optional

class Portee:
    """La portée d'UN appel d'app : pour QUI (le lecteur — un store d'un autre compte
    n'y prend rien), le droit par tableau, et les tableaux effectivement servis par elle
    (une écriture servie ainsi ne se défait pas par les routes de tableau)."""

    def __init__(self, sub: str, droit: Callable[[str], Optional[str]]):
        self.sub = sub
        self._droit = droit
        self.servis: set[str] = set()

    def droit(self, ns_id: str) -> Optional[str]:
        return self._droit(str(ns_id))


_PORTEE: ContextVar[Optional[Portee]] = ContextVar("acces_par_l_app", default=None)


@contextmanager
def portee(p: Portee) -> Iterator[Portee]:
    jeton = _PORTEE.set(p)
    try:
        yield p
    finally:
        _PORTEE.reset(jeton)


def droit(sub: Optional[str], ns_id: str) -> Optional[str]:
    """Ce que la portée en cours ouvre à `sub` sur ce tableau ; None hors portée, pour un
    autre compte, ou sinon."""
    p = _PORTEE.get()
    if p is None or sub is None or sub != p.sub:
        return None
    return p.droit(str(ns_id))


def servi(ns_id: str) -> None:
    p = _PORTEE.get()
    if p is not None:
        p.servis.add(str(ns_id))
