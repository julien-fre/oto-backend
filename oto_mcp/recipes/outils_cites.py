"""Les outils qu'une recette CITE — lus dans son corps, jamais devinés.

Une recette lancée par un agent hébergé n'appelle que les outils de la liste de son
travail (`outils_du_travail`, #1256). Jusqu'ici, la recette le découvrait au premier
appel refusé : un outil manquant par exécution, et autant d'exécutions perdues que
d'outils à ajouter. Lire ce qu'elle cite permet deux choses, avec la même liste :

- AVANT le run, sur l'agent (`runner_triggers._avec_tool_warnings`) : « la recette
  `x` de ta procédure appelle `y`, que l'agent n'a pas » — un bouton, un clic ;
- AU run (`code.executer`) : un seul refus qui nomme TOUS les outils manquants, avant
  le premier appel, plutôt que le premier d'entre eux après des écritures.

Recette déclarative : son `source.tool`. Recette-code : chaque `oto.call("<nom>"` à
nom LITTÉRAL de ses sources. Un nom construit à l'exécution ne se lit pas ici : il
reste jugé à l'appel, comme avant — la liste est un plancher, jamais un plafond.
"""
from __future__ import annotations

import re
from typing import Any

_APPEL = re.compile(r"""\boto\s*\.\s*call\s*\(\s*["']([A-Za-z0-9_]+)["']""")
#: Une procédure lance une recette en la nommant : `slug="<recette>"` près d'`oto_recipe`.
_SLUG = re.compile(r"""slug\s*[=:]\s*["`']?([a-z0-9][a-z0-9-]{1,80})""")


def outils_cites(corps: Any) -> list[str]:
    """Les outils que ce corps de recette appelle, dédupliqués, dans l'ordre."""
    if not isinstance(corps, dict):
        return []
    noms: list[str] = []
    source = corps.get("source")
    if isinstance(source, dict) and isinstance(source.get("tool"), str):
        noms.append(source["tool"])
    for texte in (corps.get("sources") or {}).values():
        if isinstance(texte, str):
            noms += _APPEL.findall(texte)
    vus: set[str] = set()
    return [n for n in noms if not (n in vus or vus.add(n))]


def recettes_citees(texte: str) -> list[str]:
    """Les slugs de recette qu'une procédure nomme (`oto_recipe … slug="x"`) — des
    candidats : l'appelant ne garde que ceux qui sont des recettes de l'org."""
    if "oto_recipe" not in (texte or ""):
        return []
    vus: set[str] = set()
    return [s for s in _SLUG.findall(texte) if not (s in vus or vus.add(s))]
