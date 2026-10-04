"""Un outil qui ne fait que LIRE chez son fournisseur le DÉCLARE, sur son décorateur :
`@mcp.tool(annotations=LECTURE)`.

La déclaration est l'annotation standard du protocole (`readOnlyHint`) : le client MCP la
voit aussi. Elle dit « cet appel ne change rien chez le tiers » — pas « il est gratuit »
(une recherche Apollo ou AI Ark facture ses crédits), ni « il ne fait pas tourner de
modèle » : un connecteur à modèle (jev, lighton) lit, mais une recette ne l'appelle pas
(`recipes/contrat.NAMESPACES_A_MODELE`).

**Ce qui la lit** : `oto_recipe`. Une recette « pull » n'appelle QUE des outils déclarés
ici — un outil non déclaré est refusé, jamais présumé lecteur : le défaut est le refus.
Un outil multiplexé par `op` ne se déclare que si TOUTES ses ops lisent.
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

LECTURE = ToolAnnotations(readOnlyHint=True)


def en_lecture(tool) -> bool:
    """L'outil (objet FastMCP) s'est-il déclaré en lecture seule ?"""
    annotations = getattr(tool, "annotations", None)
    return getattr(annotations, "readOnlyHint", None) is True
