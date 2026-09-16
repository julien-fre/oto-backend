"""Le catalogue des outils avec leur état — ce que rend `oto_list_my_tools` (oto#170).

Le catalogue est le registre BRUT du `Provider` (le même que `_resolve_tool` dans
`meta.py`) ; l'état de chaque outil pour la personne qui lit est DÉRIVÉ des couches
de masquage de la session (`session_visibility.compute_hidden_layers`), jamais
recopié d'une règle. Trois états, et une légende qui dit le geste de chacun.
"""
from __future__ import annotations

from fastmcp import Context

from .. import providers, session_visibility, tool_alias, tool_registry
from ..tool_visibility import namespace_of

# Budget d'une ligne de catalogue. ~725 entrées rendues d'un coup : chaque caractère
# est multiplié par le nombre d'outils. 100 c. suffisent à dire ce que fait un outil ;
# le détail est dans `oto_tool_schema`, qu'on lit AVANT d'appeler de toute façon.
CATALOG_BLURB = 100


def namespace_help(ns: str) -> str:
    """Ligne de catalogue du connecteur d'un namespace (curée, en français) — le pont
    entre une requête en langue naturelle et des docstrings anglaises. Fail-soft."""
    try:
        con = providers.connector_for_namespace(ns)
        return f"{con.label} {con.help}" if con else ""
    # noqa: SILENT — aide de namespace absente plutôt que fausse
    except Exception:
        return ""
# Les trois états d'un outil du catalogue, pour la personne qui le lit (oto#170).
# Dérivés des COUCHES de `session_visibility` — jamais recopiés : `installed` = dans
# aucune couche, `installable` = masqué par une couche d'AFFICHAGE seulement (l'outil
# reste appelable par `oto_call`), `not_exposed` = derrière une garde d'appel
# (activation, RBAC, bêta, plancher de rôle) ou éteint au démarrage.
ETATS = ("installed", "installable", "not_exposed")
LEGENDE = {
    "installed": "dans ta boîte à outils : appelle-le directement.",
    "installable": "appelable tout de suite par oto_call(name, arguments) ; pour l'installer "
                   "durablement : oto_connector(op='select', name=<connecteur>) — ou "
                   "oto_enable_tool(name) si c'est toi qui l'avais masqué.",
    "not_exposed": "PAS appelable : le connecteur n'est pas ouvert à ton organisation, ou "
                   "réservé à d'autres membres, ou l'outil dépasse ton rôle — un admin de "
                   "l'org l'ouvre (oto_connector_activation) ; ce n'est pas une capacité "
                   "absente.",
}


async def catalogue_avec_etat(ctx: Context, sub: str, prefix: str,
                              *, org=session_visibility._DERIVE_ORG) -> list[dict]:
    """Le catalogue ENTIER — le registre brut du `Provider`, le même que `_resolve_tool`
    (« including disabled ones ») — chaque outil avec son état pour (sub, org active).

    `org` : dérivée de la session par défaut (`_DERIVE_ORG`), mais peut être posée
    explicitement — un appelant qui vérifie un déclencheur d'une AUTRE org que la
    sienne (`oto_trigger`, avertissements des outils déclarés) n'a pas de session
    active dans cette org pour la dériver : la donner ici évite exactement le piège
    qui a coûté un run muet (payload webhook lu contre l'org du délégué, pas celle
    du travail — 16/09/2026).

    ⚠️ Jusqu'au 12/09/2026 le catalogue partait de la liste DÉJÀ FILTRÉE par la
    session (`list_tools(run_middleware=False)` après `apply_session_transforms`) :
    un outil dont le connecteur n'était pas installé, pas exposé ou réservé n'y
    figurait pas du tout, et la réponse portait `catalog_disabled_count: 0` par
    construction. Deux agents en ont conclu qu'aucun outil WhatsApp, puis Google
    Chat, n'existait (oto#170) — alors que la description promettait « every tool ».
    L'état vient des couches de masquage de la session, pas d'une règle recopiée ici."""
    from fastmcp.server.providers.base import Provider
    from fastmcp.server.server import _is_backend_tool
    from fastmcp.server.transforms.visibility import is_enabled
    # Le registre brut, moins ce qu'aucun modèle ne verra jamais : un composant
    # éteint au démarrage, ou un outil réservé à une interface (`meta.ui.visibility
    # = ["app"]`). C'est le filtre de `FastMCP.list_tools` SANS sa part de session
    # (`apply_session_transforms`) — celle-là est exactement ce que le catalogue
    # doit traverser au lieu de subir. `_is_backend_tool` est privé chez fastmcp :
    # le pin est exact, et un déplacement casse ici au premier import, pas en silence.
    bruts = [t for t in await Provider.list_tools(ctx.fastmcp)
             if is_enabled(t) and not _is_backend_tool(t)]
    couches = await session_visibility.compute_hidden_layers(ctx, sub, org=org)
    masques = set().union(*couches.values())
    non_appelables = set().union(*(noms for nom, noms in couches.items()
                                   if nom not in session_visibility.COUCHES_INSTALLABLES))
    # Le catalogue annonce les noms tels que l'utilisateur les VOIT (cf.
    # `tool_alias`) ; tout ce qui se calcule — namespace, état — repart du nom
    # canonique. Le retour `canonical(public(x)) == x` est total, donc aucun nom ne
    # se perd en route.
    entries = []
    for t in bruts:
        if t.name in non_appelables:
            etat = "not_exposed"
        elif t.name in masques:
            etat = "installable"
        else:
            etat = "installed"
        entries.append({
            "name": tool_alias.public(t.name, prefix),
            "namespace": namespace_of(t.name),
            "state": etat,
            "description": tool_registry.blurb(t.description, CATALOG_BLURB),
            "description_full": " ".join((t.description or "").split()),
            # Ligne de catalogue du connecteur : le seul texte FRANÇAIS de l'entrée
            # (les docstrings sont en anglais). Sert la recherche, pas la sortie.
            "namespace_help": namespace_help(namespace_of(t.name)),
        })
    return sorted(entries, key=lambda e: e["name"])


def grouper_par_connecteur(entries: list[dict]) -> list[dict]:
    """La projection par défaut d'`op=list` : un groupe par namespace, l'état du groupe
    et ses outils par nom — les exceptions (un outil dans un autre état que son
    connecteur : masqué par la personne, masqué par défaut, hors de portée) nommées
    à part sous `states`. Mesuré le 12/09/2026 sur 724 outils : 25 k caractères,
    contre 53 k pour une ligne par outil sans description et 115 k avec."""
    groupes: dict[str, list[dict]] = {}
    for e in entries:
        groupes.setdefault(e["namespace"], []).append(e)
    out = []
    for ns, outils in sorted(groupes.items()):
        con = providers.connector_for_namespace(ns)
        compte: dict[str, int] = {}
        for e in outils:
            compte[e["state"]] = compte.get(e["state"], 0) + 1
        etat = max(compte, key=lambda k: (compte[k], k))
        groupe = {"namespace": ns,
                  "connector": con.name if con else None,
                  "label": con.label if con else "plateforme",
                  "state": etat,
                  "tools": [e["name"] for e in outils]}
        ecarts = {e["name"]: e["state"] for e in outils if e["state"] != etat}
        if ecarts:
            groupe["states"] = ecarts
        out.append(groupe)
    return out

