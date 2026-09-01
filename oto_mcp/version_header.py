"""Garde ASGI : **toute** réponse dit quelle version l'a produite (`X-Oto-Version`).

Un endpoint `/api/version` répond à qui pense à le demander. L'en-tête, lui, se
retrouve dans le journal de celui qui n'y a pas pensé — et c'est le cas qui a
coûté une matinée le 28/08/2026 : une flotte d'agents mesurait un taux d'appels
malformés de part et d'autre de quatre mises en production, et rien dans les
traces conservées ne permettait de dire laquelle avait produit quelle mesure. Un
en-tête présent sur chaque réponse est la seule forme qui date une mesure
**rétrospectivement**, sans que personne n'ait eu à instrumenter quoi que ce soit
à l'avance.

D'où la couche ASGI plutôt qu'un ajout dans les fabriques de réponse :

- **aucun filtre de chemin** — `/mcp`, `/api/*`, les favicons, `/p/d/*` et l'app
  anonyme des sous-domaines de projet (ADR 0032) passent tous par la même racine.
  Un filtre sur `/api/` aurait raté exactement la face dont les agents se servent ;
- **on n'écrase jamais** — si un en-tête `x-oto-version` est déjà posé plus bas,
  il reste. La couche AJOUTE, elle ne réécrit pas ;
- **le corps n'est jamais lu** — pas de tampon, donc rien qui casse le streaming
  SSE de `/mcp`.

Même patron et mêmes contraintes que `response_charset.py` ; les deux se
composent dans `server.build_root_app`, sous la garde de déconnexion client.
"""
from __future__ import annotations

from . import version

_NOM = b"x-oto-version"

# Une étiquette de version est faite d'un ref git et d'un SHA : ASCII imprimable.
# On la filtre quand même — un en-tête HTTP non encodable lèverait sur CHAQUE
# réponse, et une version un peu abîmée vaut infiniment mieux qu'un serveur qui ne
# répond plus. Le jeu couvre les tags (`v1.2.3`), les branches (`origin/main`) et
# le `+` de l'étiquette.
_AUTORISES = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-_/~:"
)


def valeur_entete(etiquette: str) -> bytes:
    """L'étiquette, réduite à ce qu'un en-tête HTTP peut porter."""
    net = "".join(c for c in etiquette if c in _AUTORISES)
    return (net or version.INCONNU).encode("ascii")


class VersionHeader:
    """Ajoute `X-Oto-Version` à chaque réponse HTTP servie sous cette couche.

    L'étiquette est résolue **à la construction** (donc au montage de l'app, au
    boot) et non par requête : c'est un attribut du processus, pas de l'appel, et
    le coût par réponse se réduit à une comparaison de noms d'en-tête.
    """

    def __init__(self, app, etiquette: str | None = None) -> None:
        self.app = app
        self.entete = (_NOM, valeur_entete(etiquette or version.version_servie()))

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            # lifespan / websocket : aucune réponse HTTP à étiqueter, et pas de
            # wrapper à payer sur le chemin.
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message.get("type") == "http.response.start":
                entetes = list(message.get("headers") or ())
                if not any(cle.lower() == _NOM for cle, _ in entetes):
                    message = {**message, "headers": entetes + [self.entete]}
            await send(message)

        await self.app(scope, receive, _send)
