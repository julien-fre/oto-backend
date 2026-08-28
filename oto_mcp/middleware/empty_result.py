"""`EmptyResultMiddleware` — un résultat vide servi en PHRASE, jamais en structure nue."""
from __future__ import annotations

from fastmcp.server.middleware import Middleware

from .. import redaction


class EmptyResultMiddleware(Middleware):
    """Sert un résultat VIDE au modèle **en phrase**, jamais en structure nue.

    Un outil qui ne trouve rien rendait sa structure vide telle quelle dans le canal
    TEXTE — `{"total_count": 0, "rows": []}`. Le décodage du modèle dégénère dessus :
    il recopie la structure, boucle sur des centaines de `]}`, reprend en prose, et
    le fournisseur encadre toute la sortie comme un appel d'outil dont le nom est la
    narration. Un runner en une passe ne joue pas cet appel : le travail est perdu et
    la ligne repayée à l'identique. 16 des 26 faux départs d'une campagne, 10 des 11
    d'une vague de production (2026-08-27, otomata-tech/oto#32).

    Le défaut est invisible partout où le vide est l'exception, et dominant là où il
    est la norme — d'où une règle GÉNÉRIQUE ici, et pas un correctif par connecteur :
    n'importe quel outil qui interroge une base et n'y trouve rien passe par ce chemin.

    Doit être enregistré **juste sous `ToolAliasMiddleware`** — donc plus EXTERNE que
    tout ce qui retouche le résultat : la rédaction et l'écho de compte réémettent le
    payload en JSON dans le canal texte, et tourner avant eux ferait rétablir la
    structure qu'on vient d'en retirer. Sous `ToolAlias` parce que le gabarit se
    cherche au nom CANONIQUE de l'outil.

    Couvre les deux formes du vide, dont la plus sournoise : un outil qui rend `[]` ou
    `None` ne produit **aucun bloc de contenu** (FastMCP 3.4.2), donc un tour
    littéralement muet pour le modèle. La phrase remplace ce silence ; elle ne
    fabrique jamais de JSON pour le combler.

    Ne touche QUE le canal texte : `structuredContent` garde la structure vide pour
    les clients qui parsent (et la face REST, qui ne passe pas par cette chaîne, ne
    change pas d'un octet).
    """

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if getattr(result, "is_error", False):
            return result
        if not redaction.sert_du_vide(result):
            return result
        return redaction.render_empty(result, getattr(context.message, "name", "") or "")
