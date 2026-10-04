"""Les routes LOURDES de `/api/*` : un débit par jeton, une concurrence par route.

**Le défaut qu'il ferme** (#1141, infra#9). Le 04/10/2026, un seul client a appelé une
route d'agrégat 300 à 600 fois par minute ; chaque appel pouvait tenir une connexion
plusieurs secondes. Le pool de connexions s'est vidé pour TOUT le serveur : des
centaines de réponses 500 sur des routes qui n'avaient rien demandé, dont la file des
agents hébergés. Une route lourde ne doit pas pouvoir prendre le pool aux autres.

**Ce qu'il fait.** Pour chaque route déclarée dans `ROUTES_LOURDES` — le SEUL endroit
où une route devient lourde —, deux bornes, dans cet ordre, toutes deux sans attente :

1. **le débit par jeton** : au plus `par_minute` appels sur une fenêtre glissante de
   60 s, comptés par empreinte du jeton porté (`Authorization`), jamais par le jeton
   lui-même. Au-delà : `429 rate_limited`, `Retry-After` = le temps qu'il faut pour que
   le plus ancien appel sorte de la fenêtre ;
2. **la concurrence par route** : au plus `concurrence` requêtes en cours à la fois
   dans ce processus. Au-delà : `503 route_busy`, `Retry-After: 2`. Un appel refusé
   ici ne consomme pas de débit.

Le refus part AVANT le reste de la chaîne — ni authentification, ni handler : il ne
coûte au pool que la ligne du journal REST (`RestCallLogger`, au-dessus), écrite hors
de la boucle comme pour tout appel, et qui garde le refus visible. Corps JSON de l'enveloppe d'erreur ordinaire
(`{error, detail, details}`), avec les en-têtes CORS de l'origine : un navigateur le lit.

**Ce qu'il ne fait pas.** Les compteurs vivent dans le PROCESSUS : deux processus
(bascule bleu/vert, canari) ont chacun les leurs. C'est une protection du pool de CE
processus, lui aussi par processus — pas un quota de facturation. Le filtre global par
adresse reste celui du proxy. Pass-through total hors des routes déclarées : n'altère
ni `/mcp` ni le reste de `/api/*`.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .base import _cors_headers

_FENETRE_S = 60.0
# Au-delà, on purge les jetons qui n'ont plus rien dans leur fenêtre : la table ne
# grossit pas sans fin avec le nombre de jetons vus.
_PURGE_AU_DELA = 10_000
_ATTENTE_SATURATION_S = 2


@dataclass(frozen=True)
class RouteLourde:
    """Une route lourde : sa méthode, son gabarit de chemin (paramètres entre
    accolades, comme dans la table des routes) et ses deux bornes. `0` désarme une
    borne."""
    methode: str
    gabarit: str
    par_minute: int
    concurrence: int
    pourquoi: str
    motif: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        segments = [re.escape(s) for s in re.split(r"\{[^/{}]+\}", self.gabarit)]
        object.__setattr__(self, "motif", re.compile("^" + "[^/]+".join(segments) + "$"))

    def vise(self, methode: str, chemin: str) -> bool:
        return methode == self.methode and bool(self.motif.match(chemin))


#: Les routes lourdes de `/api/*` — LE seul endroit où une route le devient. Une route
#: s'y inscrit avec sa raison : ce qu'elle coûte, et ce qu'un client qui la martèle
#: priverait aux autres.
#:
#: Vide à la naissance du mécanisme, et délibérément : `POST /api/me/runner/jobs` n'y
#: entre PAS. Le 04/10 elle a été la VICTIME du pool vide (52 des réponses 500), pas sa
#: cause, et ses appelants sont les workers d'agents hébergés, dont le sondage est
#: régulier par construction : la brider ralentirait la file sans rien protéger. La
#: route d'agrégat qui a vidé le pool s'y inscrit avec le lot qui la reprend.
ROUTES_LOURDES: tuple[RouteLourde, ...] = ()


def _empreinte(scope: dict) -> str:
    """La clé de débit : l'empreinte du jeton porté, jamais le jeton. Sans jeton,
    l'adresse du client — la requête sera refusée plus loin, mais elle ne passe pas
    au travers du débit pour autant."""
    for cle, valeur in scope.get("headers", []):
        if cle == b"authorization" and valeur:
            return "j:" + hashlib.sha256(valeur).hexdigest()[:32]
    client = scope.get("client") or ("?", 0)
    return f"a:{client[0]}"


class _Compteurs:
    def __init__(self) -> None:
        self.verrou = threading.Lock()
        self.appels: dict[tuple[str, str], deque] = {}
        self.en_cours: dict[str, int] = {}


class GardeRoutesLourdes:
    """Middleware ASGI : borne le débit par jeton et la concurrence des routes
    déclarées (`ROUTES_LOURDES`). Compteurs propres à l'instance, donc au processus."""

    def __init__(self, app, routes: Optional[Iterable[RouteLourde]] = None):
        self.app = app
        self.routes = tuple(ROUTES_LOURDES if routes is None else routes)
        self._c = _Compteurs()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.routes:
            return await self.app(scope, receive, send)
        route = next((r for r in self.routes
                      if r.vise(scope.get("method", ""), scope.get("path", ""))), None)
        if route is None:
            return await self.app(scope, receive, send)

        refus = self._prendre(route, _empreinte(scope), time.monotonic())
        if refus is not None:
            return await self._refuser(scope, send, *refus)
        try:
            await self.app(scope, receive, send)
        finally:
            with self._c.verrou:
                self._c.en_cours[route.gabarit] -= 1

    def _prendre(self, route: RouteLourde, cle: str, maintenant: float):
        """Passe les deux bornes ; rend `None` (place prise) ou le refus à servir."""
        with self._c.verrou:
            fenetre = None
            if route.par_minute > 0:
                fenetre = self._c.appels.setdefault((route.gabarit, cle), deque())
                while fenetre and maintenant - fenetre[0] >= _FENETRE_S:
                    fenetre.popleft()
                if len(fenetre) >= route.par_minute:
                    attente = max(1, math.ceil(_FENETRE_S - (maintenant - fenetre[0])))
                    return (429, "rate_limited",
                            f"trop d'appels sur cette route : au plus {route.par_minute} "
                            f"par minute et par jeton. Réessaie dans {attente} s.",
                            attente, {"limit_per_minute": route.par_minute})
            en_cours = self._c.en_cours.get(route.gabarit, 0)
            if route.concurrence > 0 and en_cours >= route.concurrence:
                return (503, "route_busy",
                        f"cette route sert déjà {route.concurrence} requêtes en même "
                        "temps : réessaie dans quelques secondes.",
                        _ATTENTE_SATURATION_S, {"max_concurrent": route.concurrence})
            self._c.en_cours[route.gabarit] = en_cours + 1
            if fenetre is not None:
                fenetre.append(maintenant)
                self._purger(maintenant)
        return None

    def _purger(self, maintenant: float) -> None:
        if len(self._c.appels) > _PURGE_AU_DELA:
            for k in [k for k, v in self._c.appels.items()
                      if not v or maintenant - v[-1] >= _FENETRE_S]:
                del self._c.appels[k]

    async def _refuser(self, scope, send, statut, code, detail, attente, extra):
        corps = json.dumps({
            "error": code, "detail": detail,
            "details": {"retryable": True, "retry_after_seconds": attente, **extra},
        }).encode()
        origine = next((v.decode("latin-1") for k, v in scope.get("headers", [])
                        if k == b"origin"), None)
        entetes = [(b"content-type", b"application/json"),
                   (b"content-length", str(len(corps)).encode()),
                   (b"retry-after", str(attente).encode())]
        entetes += [(k.lower().encode(), v.encode())
                    for k, v in _cors_headers(origine).items()]
        await send({"type": "http.response.start", "status": statut, "headers": entetes})
        await send({"type": "http.response.body", "body": corps})
