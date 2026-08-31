"""L'erreur de protocole MCP, sous un nom qui ne bouge pas.

Le SDK amont a renommé sa classe d'erreur `McpError` → `MCPError`. Deux noms, aucun
recouvrement : la version d'avant n'expose que l'ancien, celle d'après que le nouveau.
213 fichiers l'importaient en direct — donc le renommage a cassé le CI d'un coup, sur
toutes les branches à la fois, pendant que les environnements de développement plus
anciens restaient verts. La divergence était invisible là où on travaille et totale là
où on vérifie.

**Ce module est le seul endroit du dépôt qui nomme la classe amont.** Le reste importe
`McpError` d'ici et ne sait pas — ne doit pas savoir — comment elle s'appelle en face.

Pourquoi pas simplement épingler la version : `mcp` n'est même pas une dépendance
déclarée, elle arrive derrière `fastmcp`. La contraindre nous rendrait responsables de
sa compatibilité avec un paquet qu'on ne choisit pas, et repousserait le problème au
prochain renommage. Ici, les deux noms marchent, et le troisième se traitera en une
ligne — à un seul endroit.

⚠️ **L'ordre d'essai n'est pas neutre** : le nom RÉCENT d'abord. Une version qui
porterait les deux (alias de transition) doit nous voir prendre celui qui reste, pas
celui qui part.
"""
from __future__ import annotations

try:                                  # SDK récent
    from mcp.shared.exceptions import MCPError as McpError
except ImportError:                   # SDK d'avant le renommage
    from mcp.shared.exceptions import McpError    # noqa: F401

__all__ = ["McpError"]
