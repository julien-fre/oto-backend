"""`ToonTextChannelMiddleware` — le canal TEXTE en TOON quand, et seulement quand,
c'est plus court que le JSON qu'il remplace.

Le poste dominant d'un résultat de liste, c'est le nom de chaque colonne réécrit à
chaque ligne. TOON (`oto_mcp/toon.py`) le déclare une fois puis écrit une ligne par
enregistrement. Mesuré le 08/09/2026 sur des charges réelles : 33 % sur un relevé de
monitoring, 34 % sur une page de recherche de profils déjà projetée.

⚠️ **La décision se prend par CHARGE, pas par outil.** Le même outil sert des tables
de compteurs et des tables de prose : mesuré le même jour, une table à prose gagne
12,5 % *au mieux*, et **perd 7,6 %** telle quelle — une seule ligne à qui il manque
une colonne fait basculer le bloc en forme de liste, plus verbeuse que le JSON. Une
liste blanche d'outils aurait donc embarqué la régression avec le gain. Ici on encode,
on compare la longueur, et on ne remplace que si le TOON est plus court d'au moins la
marge : le cas défavorable ne se produit pas, il se refuse.

**Place dans la chaîne** (l'ordre d'enregistrement est un contrat,
`docs/conventions.md`) : entre `CallContext` et `FieldRedaction`, donc

- **plus EXTERNE que la rédaction** : la rédaction réémet le canal texte en JSON, et
  tourner sous elle ferait rétablir le JSON qu'on vient d'en retirer. Plus grave,
  son `extract_payload` ne sait pas relire du TOON : elle rendrait `None`, une policy
  existante ne s'appliquerait plus et la sortie partirait NON RÉDIGÉE. Cet échec-là
  est ouvert, pas fermé — d'où la place, et pas seulement l'ordre de rendu ;
- **plus INTERNE que `EmptyResult`**, qui juge le vide en relisant le texte en JSON.
  Un vide rendu en TOON le rendrait aveugle, et la phrase qu'il sert à la place d'une
  structure vide ne partirait plus. On ne touche donc JAMAIS un résultat vide, et
  c'est lui qui tranche ensuite ;
- **plus INTERNE que `CallContext`**, dont la ContextVar est encore posée si un jour
  la décision doit dépendre de l'org.

**Le canal structuré n'est pas touché.** `structuredContent` garde son JSON pour les
clients qui parsent, comme le fait déjà le rendu du vide. Ce que fait le client de ce
second canal décide de la TAILLE du gain, et le raisonnement se BORNE, il ne se
suppose pas — deux cas, et aucun des deux ne régresse :

- le client ne donne que `content` au modèle : le gain est celui qu'on mesure ;
- le client lui donne les DEUX : il lisait déjà la même donnée en double (la spec MCP
  demande le JSON sérialisé dans `content` en plus du structuré). On passait alors
  JSON + JSON, on passe TOON + JSON. Le gain se réduit environ de moitié, il ne
  disparaît pas.

Le plancher est donc « pas pire qu'aujourd'hui », par construction et non par pari.
Ce qui reste à mesurer est l'écart entre ces deux cas, et ça se lit chez le CLIENT :
le serveur ne journalise pas la taille de ce qu'il rend (`calllog.py` ne sérialise
même pas le structuré), donc aucune lentille de monitoring ne le dira. Protocole :
même requête, drapeau éteint puis allumé, on compare le contexte consommé côté client.
Couper le canal structuré à l'aveugle casserait les clients qui en dépendent pour un
bénéfice supposé — c'est exactement le raisonnement qu'on refuse ailleurs.

**Actif par défaut** : `OTO_TOON_TEXT_CHANNEL=0` l'éteint. Rien ne peut rallonger une
réponse (le TOON ne part que s'il raccourcit), et 74 fichiers de tests à chaîne MCP
réelle rendent les mêmes résultats drapeau éteint ou allumé (mesuré le 10/09/2026).

⚠️ **Qui le lit vraiment, mesuré le 10/09/2026.** Claude Code donne au modèle
`structuredContent` À LA PLACE du texte (marqueurs distincts sur les deux canaux, 3 runs
sur 3, avec ou sans schéma de sortie) ; `oto-runner` fait de même (`mcp.py` : le canal
structuré d'abord). Pour ces clients, et pour tout outil qui rend un `dict`, ce TOON
n'est donc PAS lu tant que le canal structuré est servi. Il profite aux clients qui ne
lisent que le texte. Et quand l'écho de compte s'applique (plusieurs comptes nommés),
`CallContextMiddleware`, plus externe, réémet le payload en JSON : le TOON est alors
défait, sans erreur.
"""
from __future__ import annotations

import json
import logging
import os

from fastmcp.server.middleware import Middleware

from .. import redaction, toon

logger = logging.getLogger(__name__)

# Sous ce seuil, le gain se compte en dizaines de tokens : on ne paie ni le parse ni
# la divergence de format pour ça. Mesuré : un enregistrement seul rend 4 %.
TAILLE_MIN = 1_000

# Condition NÉCESSAIRE d'un bloc tabulaire dans le texte servi : une liste d'objets.
# Un test de sous-chaîne, avant tout parse — le chemin chaud est le cas où il n'y en
# a pas, et il doit rester à coût nul (le serveur est mono-loop).
_MARQUEUR_LISTE = "[{"


def actif() -> bool:
    return os.environ.get("OTO_TOON_TEXT_CHANNEL", "1") != "0"


def _texte_servi(result) -> str | None:
    """Le texte du PREMIER bloc de contenu, celui qui part au modèle. `None` sinon."""
    content = getattr(result, "content", None) or []
    if len(content) != 1:
        # Plusieurs blocs (ou aucun) : on ne saurait pas lequel remplacer sans
        # changer ce que le modèle lit. On ne touche pas.
        return None
    texte = getattr(content[0], "text", None)
    return texte if isinstance(texte, str) else None


def _reemettre(result, texte: str):
    """Réémet le `ToolResult` avec `texte` dans le canal texte, le reste intact."""
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent
    return ToolResult(
        content=[TextContent(type="text", text=texte)],
        structured_content=getattr(result, "structured_content", None),
        meta=getattr(result, "meta", None),
    )


class ToonTextChannelMiddleware(Middleware):
    """Réécrit le canal texte en TOON quand la charge y gagne. Cf. le module."""

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if not actif() or getattr(result, "is_error", False):
            return result
        texte = _texte_servi(result)
        if texte is None or len(texte) < TAILLE_MIN or _MARQUEUR_LISTE not in texte:
            return result
        try:
            charge = json.loads(texte)
        except (ValueError, TypeError):
            return result
        # Un résultat vide appartient à `EmptyResultMiddleware`, plus externe, qui le
        # rend en phrase après nous — et qui le juge en relisant le texte en JSON.
        # On lui laisse la main en réutilisant SON prédicat (« derive don't
        # duplicate »), nourri de notre propre lecture pour ne pas parser deux fois.
        if redaction.is_empty_payload(charge):
            return result
        try:
            rendu = toon.choisir(charge, texte)
        except Exception:
            # L'encodage n'est jamais une raison de perdre un résultat que le handler
            # a produit : on journalise en nommant, et on sert le JSON (interdiction
            # du silence, `docs/conventions.md`).
            logger.exception(
                "encodage TOON en échec pour %s — le JSON est servi tel quel",
                getattr(context.message, "name", "") or "?",
            )
            return result
        if rendu is None:
            return result
        return _reemettre(result, rendu)
