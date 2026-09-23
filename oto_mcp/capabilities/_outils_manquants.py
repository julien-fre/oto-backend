"""La borne de `op=launch` : les outils déclarés d'une flotte sont-ils MONTÉS
dans l'org visée (oto-backend, incident du 22/09) ?

Le 22/09 de 12:54 à 15:16 UTC, l'org maison du compte porteur d'une flotte a
basculé (cf. #1058) et sa boîte a perdu tous ses connecteurs. Les passes D, E
et F ont continué d'écrire — 80 fiches chez une cliente, une partie sans
registre ni recherche web — parce que rien n'arrêtait ni ne signalait
l'absence des outils déclarés au moment où ça comptait : l'agent ne l'a dit
que dans ses notes de sortie, après coup.

⚠️ **Volontairement SYNC et ÉTROIT.** Ce n'est PAS `session_visibility` : on ne
recalcule pas la visibilité complète d'une session (toggles perso, RBAC,
bêta) — seulement si le CONNECTEUR d'un outil déclaré est exposé à l'org et
sélectionné actif pour le `sub` porteur de la flotte, les deux couches qui
gouvernent l'INSTALLATION (`ADR 0011`/`ADR 0019`) et qui sont celles qui ont
manqué le 22/09. `runner_fleets._fleets` reste un handler SYNC (docs/event-
loop-perf.md) : passer par `session_visibility.compute_hidden_tools` (async,
lié à l'instance FastMCP) forcerait tout le handler à devenir async, donc
toute sa plomberie SQL à tourner sur la boucle — exactement le mode de gel que
la garde d'exécution de #1049 existe pour attraper. On réutilise directement
les fonctions SYNC déjà lues par `_compute_couches`.

⚠️ Ne SEED rien : `is_seeded`/`seed_active` (le socle par défaut) sont un
effet de bord du premier `initialize` d'une session réelle, pas de ce contrôle
en lecture. Un `(sub, org)` jamais vu ici lira `list_selection` vide et tout
connecteur déclaré sera donc nommé manquant — fail-CLOSED assumé : mieux vaut
un refus au lancement qu'une passe qui écrit sans ses outils."""
from __future__ import annotations

from .. import providers
from ..connectors import activation as connector_activation
from ..connectors import selection as connector_selection
from ..tool_visibility import namespace_of


def manquants(org_id: int, sub: str, tools) -> list[str]:
    """Les noms de `tools` dont le connecteur n'est ni exposé à `org_id`, ni
    sélectionné `ACTIVE` par `sub` dans cette org. Un outil SPINE (aucun
    connecteur au registre — `data_*`, `run_*`, `oto_*`…) n'est jamais nommé :
    rien ne le masque à ce grain (garde anti-lockout de `session_visibility`)."""
    if not tools:
        return []
    # Résolus PARESSEUSEMENT : une flotte purement spine (`data_*`/`run_*`) ne
    # doit lire ni l'exposition ni la sélection — deux requêtes évitées, et
    # les bancs qui ne parlent que de spine n'ont rien à stubber côté base.
    exposed = None
    selection = None
    out = []
    for name in tools:
        if not isinstance(name, str):
            continue
        con = providers.connector_for_namespace(namespace_of(name))
        if con is None:
            continue  # spine, jamais gaté à ce grain
        if exposed is None:
            exposed = connector_activation.exposed_connectors(org_id)
            selection = connector_selection.list_selection(sub, org_id or 0)
        if con.name not in exposed or selection.get(con.name) != connector_selection.ACTIVE:
            out.append(name)
    return out
