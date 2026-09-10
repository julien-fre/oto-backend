"""`MarkdownBodyMiddleware` — un corps markdown se sert en markdown, pas en JSON.

Un outil qui rend un dict dont un champ `body_md` porte l'essentiel (une procédure,
un guide, un document) est servi sur le canal TEXTE comme du JSON : le corps y arrive
en chaîne échappée — chaque retour à la ligne devient `\\n`, chaque guillemet `\\"`,
et une procédure en compte des centaines. Mesuré le 10/09/2026 sur Haiku : **+4 à
+7 %** de jetons sur le même texte (un dessin de 3 304 caractères : 983 → 1 048 ;
2 845 caractères de prose : 781 → 814), payés à chaque lecture, puis à chaque tour
tant que le corps reste dans le contexte.

Ce middleware réémet le canal texte en **markdown** : les autres champs du dict en
en-tête, une ligne `clé: valeur` chacun (les structures en JSON compact), une ligne
vide, puis le corps tel quel. Le canal STRUCTURÉ ne bouge pas : un client qui parse
garde son JSON.

Ne s'applique qu'à un dict dont `body_md` fait au moins la MOITIÉ du payload — la
forme d'une fiche. Une liste de fiches, une enveloppe, un résultat sans corps sont
servis comme avant.

**Place dans la chaîne** : juste sous `EmptyResult`, donc plus EXTERNE que tout ce qui
réémet le résultat sur les deux canaux en JSON (l'écho de compte, la rédaction) — plus
interne, l'un d'eux rétablirait le JSON qu'on vient de remplacer. Il lit la structure
APRÈS la rédaction : un champ rédigé ne revient pas par l'en-tête.
"""
from __future__ import annotations

import json

from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

CLE = "body_md"
# Sous cette part, le dict n'est pas une fiche : le corps n'y est qu'un champ parmi
# d'autres, et l'en-tête pèserait autant que ce qu'on retire.
PART_MIN = 0.5


def rendu(charge: dict) -> str | None:
    """Le texte markdown d'une fiche, ou `None` si `charge` n'en est pas une."""
    corps = charge.get(CLE)
    if not isinstance(corps, str) or not corps.strip():
        return None
    compact = json.dumps(charge, ensure_ascii=False, separators=(",", ":"))
    if len(corps) < PART_MIN * len(compact):
        return None
    lignes = []
    for cle, valeur in charge.items():
        if cle == CLE or valeur is None:
            continue
        if isinstance(valeur, (dict, list)):
            valeur = json.dumps(valeur, ensure_ascii=False, separators=(",", ":"))
        lignes.append(f"{cle}: {valeur}")
    return "\n".join(lignes) + "\n\n" + corps


class MarkdownBodyMiddleware(Middleware):
    """Sert une fiche à corps markdown en markdown sur le canal texte. Cf. le module."""

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if getattr(result, "is_error", False):
            return result
        charge = getattr(result, "structured_content", None)
        if not isinstance(charge, dict):
            return result
        texte = rendu(charge)
        if texte is None:
            return result
        return ToolResult(
            content=[TextContent(type="text", text=texte)],
            structured_content=charge,
            meta=getattr(result, "meta", None),
        )
