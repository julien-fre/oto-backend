"""Un porteur de secret ne se raconte pas — le `repr` expurgé des objets de la cascade.

⚠️ **Le `repr` par défaut d'un dataclass imprime TOUS ses champs**, secret compris.
Ce n'est pas une coquetterie de log : c'est le canal par lequel une clé déchiffrée
sort du serveur sans que personne ne l'ait écrit. Trois chemins, tous réels :

- un `logger.debug("%r", rc)` posé de bonne foi par un lot ultérieur ;
- un **traceback** — une frame qui lève garde ses locales, et le collecteur
  d'erreurs les sérialise (oto-backend#564 : `include_local_variables` valait `True`
  par défaut, chaque exception repartait avec la pile entière) ;
- toute sérialisation générique d'un état (dump de diagnostic, message d'assertion).

D'où la règle, et l'endroit où elle se pose : **sur l'OBJET, pas sur la variable**.
C'est l'objet qui voyage — une frame le tient sous un nom, une autre sous un autre,
et fermer les deux ou trois fonctions qui le construisent ne ferme rien du tout.
Deux dataclasses portent aujourd'hui un secret déchiffré : `ResolvedCredential`
(le credential gagnant) et `CascadeRung` (le barreau gagnant de la marche, dont le
`payload` EST le secret en mode fetch).

Voisin de `oto_mcp/journal_secrets.py`, la même règle sous un autre angle : là-bas
ce qu'on ÉCRIT dans le journal, ici ce qu'un objet DIT de lui-même. Fond et
historique : `docs/monitoring.md` §Error tracking.
"""
from __future__ import annotations

from dataclasses import fields

_EXPURGE = "<expurgé>"


def expurge(obj, *caches: str) -> str:
    """`repr` du dataclass `obj`, les champs nommés remplacés par `<expurgé>`.

    ⚠️ **Un nom de champ inconnu LÈVE.** C'est le mode d'échec qui compte ici :
    une faute de frappe (`"secrret"`) rendrait la protection muette — le `repr`
    continuerait d'imprimer la clé, et rien ne le dirait. Le seul moment où on
    peut s'en apercevoir est celui-ci.
    """
    connus = {f.name for f in fields(obj)}
    inconnus = [c for c in caches if c not in connus]
    if inconnus:
        raise ValueError(
            f"{type(obj).__name__} n'a pas de champ {inconnus} — expurger un champ "
            "qui n'existe pas ne protège rien et ne se voit nulle part.")
    dedans = ", ".join(
        f"{f.name}=" + (_EXPURGE if f.name in caches else repr(getattr(obj, f.name)))
        for f in fields(obj))
    return f"{type(obj).__name__}({dedans})"
