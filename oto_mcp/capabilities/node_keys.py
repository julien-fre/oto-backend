"""Les clés d'origine d'un nœud, dérivées en UN seul endroit.

Un nœud issu de la conversion garde sa source dans `props.legacy` / `props.legacy_id`
(`db/nodes.py`) : une page est un `doc`, un projet un `prj`. Deux surfaces servent
cette clé — la fiche (`node_view`) et le rail (`shell`) — et la règle ne doit donc
vivre dans aucune des deux.

⚠️ **Pourquoi un module à part plutôt qu'un import de l'une vers l'autre** : les deux
DÉCLARENT des capacités, et l'ordre d'enregistrement des routes REST est un contrat
figé (Starlette sert le premier match). Faire importer l'une par l'autre réordonne la
table sans rapport avec le sujet. Ce module n'enregistre rien : il peut être importé
de partout sans déplacer une route.

Il ne porte que de la dérivation pure — aucun accès base, aucun modèle servi.
"""
from __future__ import annotations

from typing import Optional


def doc_id_de(legacy: Optional[str], legacy_id) -> Optional[int]:
    """La poignée `doc_id` d'un nœud, à partir de sa seule clé legacy.

    ⚠️ **Écrite ICI et nulle part ailleurs.** La règle du dépôt vaut pour une
    dérivation comme pour un prédicat SQL : deux endroits qui l'écrivent finissent par
    diverger, et celui qui se trompe ne le montre pas — il rend l'entier d'une autre
    page, qui s'ouvre sans erreur.

    `None` dès que la source n'est pas une page : un projet, un tableau natif ou une
    procédure n'ont pas de document derrière eux, et deviner en fabriquerait un faux.
    La colonne SQL rend du texte, d'où la conversion.
    """
    return int(legacy_id) if legacy == "doc" and legacy_id is not None else None
