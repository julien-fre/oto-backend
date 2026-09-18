"""Un seul canal porte la donnée — le canal STRUCTURÉ se mérite, il ne se déduit pas.

Un résultat d'outil MCP a deux canaux : `content` (du texte, ce qu'un modèle lit) et
`structuredContent` (un JSON validable contre l'`outputSchema` de l'outil, ce qu'un
client qui PARSE consomme). La spec exige le second dès qu'un schéma est déclaré.

FastMCP déclare ce schéma **par inférence** : toute fonction annotée `-> dict` reçoit
`{"type": "object", "additionalProperties": true}` — « un objet, tout est permis » — et
donc un canal structuré. Recompté le 10/09/2026 sur le montage COMPLET (`_build_mcp`,
717 outils) : **473** portaient ce schéma déduit vide, 124 n'en avaient aucun, et 120
gardent l'enveloppe `x-fastmcp-wrap-result` (un retour annoté `-> list` ou `-> object`,
emballé en `{"result": …}`) — **zéro dont le schéma décrive un seul champ**. Le contrat
typé, seule raison d'être du canal, n'existe pas ; la copie, elle, part à chaque appel.

Et cette copie n'est pas inerte : **Claude Code et oto-runner donnent au modèle le canal
structuré À LA PLACE du texte** (marqueurs distincts sur les deux canaux, trois runs sur
trois, avec ou sans schéma déclaré). Tout ce que la chaîne optimise dans le texte — le
rendu du vide, la rédaction, le TOON — est donc servi à un canal que ces clients ne
lisent pas, et la copie brute part à leur place.

D'où la règle, tenue ici en deux gestes qui vont ensemble :

1. **au montage**, `retirer_les_schemas_vides` efface le schéma DÉDUIT de chaque outil
   (l'objet vide) — un outil neuf naît sans contrat de sortie, sauf s'il en déclare un
   vrai ; un client ne peut plus compter sur un `outputSchema` qui ne disait rien ;
2. **à l'appel**, ce middleware retire `structuredContent` des outils qui n'ont **plus**
   de schéma. Sans lui, FastMCP émettrait encore le canal pour rien, et Claude Code
   continuerait de le lire.

⚠️ **Les deux gestes sont indissociables, et dans cet ordre.** Un client conforme
VALIDE : le client FastMCP refuse un résultat sans canal structuré dès qu'un schéma est
annoncé (« outputSchema defined but no structured output returned », mesuré sur le banc
le 10/09/2026). Le middleware ne juge donc que l'ABSENCE de schéma — un outil dont le
schéma vide est encore déclaré garde son canal, quoi qu'il vaille. C'est le montage qui
retire le schéma, et le middleware qui suit ; jamais l'inverse.

Les 120 enveloppes `x-fastmcp-wrap-result` sont **gardées** : là, les deux canaux n'ont
pas la même forme (le texte porte la valeur nue, le structuré `{"result": …}`), et un
client qui parse `.result` ne retrouverait pas la donnée dans le texte sans la
désemballer. Elles sont nommées dans `tests/structured_output_debt.txt`, liste qui ne
peut que décroître ; un outil neuf s'annote `-> dict` et rend un dict aux clés nommées
(leçon `pennylaneged`), ou déclare un vrai `Output` — c'est l'annotation `-> list` ou
`-> object` qui fabrique l'enveloppe.

**Place dans la chaîne** : juste sous `ToolAlias` (il lui faut le nom CANONIQUE), donc
plus EXTERNE que tout ce qui réémet le résultat sur les deux canaux — le rendu du vide,
la rédaction, l'écho de compte. Plus interne, l'un d'eux rétablirait le canal qu'on
vient de retirer.

**Exception : une app** (outil qui déclare `_meta.ui.resourceUri`, extension MCP Apps).
Là, `structuredContent` n'est pas la copie du texte : c'est l'UNIQUE entrée de la carte
— l'hôte le pousse à la vue (`ui/notifications/tool-result`) et ne le donne pas au
modèle. Le renderer Prefab ne peint rien sans lui et reste sur « Waiting for
content… » (`prefab_ui/renderer/app.html` : `const p=f.structuredContent; if(!p)return`).
Le retirer a éteint TOUTES les apps du 10/09 au 18/09/2026 (signal #1083,
`oto_doc_app`). Une app garde donc son canal ; le texte, lui, est à sa charge.

Ce qui rend ça durable : **le comportement du client cesse de compter.** Quand un seul
canal porte la donnée, Claude Code, oto-runner et un client tiers lisent tous la même
chose — celle que la chaîne a optimisée.
"""
from __future__ import annotations

from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import Tool, ToolResult

from .call_context import _cible


# Le schéma que FastMCP DÉDUIT d'un `-> dict` : il ne décrit rien.
_OBJET_VIDE = {"type": "object", "additionalProperties": True}


def schema_est_vide(schema) -> bool:
    """Vrai pour un schéma qui ne décrit aucun champ — absent, ou l'objet vide déduit.

    Une enveloppe `x-fastmcp-wrap-result` n'est PAS vide au sens de cette règle : elle
    change la forme entre les deux canaux, et c'est cette différence qu'un client
    pourrait avoir apprise."""
    return schema is None or schema == _OBJET_VIDE


def est_une_app(outil) -> bool:
    """Vrai pour un outil MCP App : il déclare la ressource d'UI qui le peint
    (`_meta.ui.resourceUri`). Son canal structuré nourrit la carte — cf. le module."""
    ui = (getattr(outil, "meta", None) or {}).get("ui")
    return isinstance(ui, dict) and bool(ui.get("resourceUri"))


def retirer_les_schemas_vides(instance) -> frozenset[str]:
    """Efface le schéma de sortie DÉDUIT de chaque outil monté ; rend les noms de ceux
    qui gardent le leur — l'ensemble que `tests/structured_output_debt.txt` fige.

    À appeler après le DERNIER montage (`register_all` puis l'adaptateur de capacités) :
    un outil monté ensuite garderait son schéma déduit, et `tests/middleware/
    test_un_seul_canal.py` le nommerait.

    ⚠️ Lit le magasin de composants du fournisseur local de FastMCP (3.4, épinglé
    `<3.5` par `pyproject.toml`) : l'énumération publique est asynchrone, et ce montage
    est synchrone. Si l'attribut disparaît à une montée de version, ce n'est pas un
    silence : le montage lève, et `test_server_construction.py` le dit.
    """
    gardes: set[str] = set()
    for composant in list(instance.local_provider._components.values()):
        if not isinstance(composant, Tool):
            continue
        if schema_est_vide(composant.output_schema):
            composant.output_schema = None
        else:
            gardes.add(composant.name)
    return frozenset(gardes)


class UnSeulCanalMiddleware(Middleware):
    """Retire `structuredContent` des outils SANS schéma de sortie. Cf. le module.

    La décision se lit sur l'outil AU MOMENT de l'appel (`get_tool`), pas sur un
    ensemble figé au montage : un outil monté plus tard, ou un banc de test qui copie
    cette chaîne sur un serveur d'un seul outil, est jugé sur SON schéma. Une lecture
    par appel, dans le magasin local — pas un aller-retour.
    """

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        if getattr(result, "is_error", False):
            return result
        if getattr(result, "structured_content", None) is None:
            return result
        # `oto_call` dispatche un autre outil (ADR 0036) : c'est la CIBLE qui décide
        # si une enveloppe est à garder, pas le nom du dispatch.
        name = getattr(context.message, "name", "") or ""
        args = getattr(context.message, "arguments", None) or {}
        # `get_tool` rend `None` pour un nom inconnu (fastmcp 3.4) : on ne sait alors
        # rien du schéma, et on ne retire rien à un appel qui a déjà réussi.
        outil = await context.fastmcp_context.fastmcp.get_tool(_cible(name, args))
        # ABSENT, pas « vide » : un schéma encore déclaré, fût-il l'objet vide, exige
        # le canal aux yeux d'un client qui valide (cf. l'en-tête du module).
        if outil is None or getattr(outil, "output_schema", None) is not None:
            return result
        # Une app : le canal structuré EST la carte, pas une copie du texte.
        if est_une_app(outil):
            return result
        return ToolResult(
            content=result.content,
            structured_content=None,
            meta=getattr(result, "meta", None),
        )
