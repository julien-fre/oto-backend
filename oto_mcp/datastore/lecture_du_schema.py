"""LIRE un schéma autrement que tel qu'il est servi : sa forme COMPACTE (oto#35) et
ses GARDES telles que la plateforme les applique (oto#94).

Module PUR (aucun I/O) : le store rend le schéma, ce module le reformule.

## La forme compacte (oto#35)

Inspecter un schéma coûtait des milliers de mots : la prose écrite pour l'agent
(`description`, souvent de plusieurs paragraphes), les libellés, la zone libre, les clés
nulles. Quatre contraintes inertes ont été manquées en deux jours sur des schémas de
production, en partie parce qu'il y avait trop à lire.

La forme compacte garde, à chaque niveau, les clés que le VALIDATEUR lit
(`schema_keys.CONTRAINTES`) — structure et contraintes — et retire les clés nulles. Elle
est DÉRIVÉE des lecteurs déclarés, jamais d'une liste tenue ici.

⚠️ **Une forme compacte ne se repose JAMAIS.** `data_set_schema` remplace le schéma
entier : reposer une forme compacte effacerait toutes les descriptions et tous les
libellés du tableau. C'est une lecture d'inspection, la réponse le dit (`forme`).

## Les gardes (oto#94)

Un propriétaire qui déclare un verrou n'avait aucun moyen d'apprendre qu'il est
appliqué, ICI, sur ce tableau. La seule sonde (`enforced`) répond à une question
voisine — « cette version sait appliquer cette clé » — qui reste vraie quand le tableau
ne la porte pas, ou la porte sans effet. Une affirmation fausse (« le verrou ne mord
pas sur les colonnes texte ») a coûté une demi-journée à infirmer.

`gardes` rend, colonne par colonne, ce que les prédicats qui DÉCIDENT retiennent :
`readonly_fields`, `acces_agent.masquees`/`fermees` — les mêmes que la sortie et le
refus consultent, jamais une copie de leur règle. Et `sans_effet` nomme ce qui est
déclaré et que ces prédicats laissent tomber, avec la raison.
"""
from __future__ import annotations

from typing import Any, Optional

from . import acces_agent as aga
from . import cles_inconnues
from . import schema_keys as sk
from .declaration import readonly_fields
from .non_applique import lifecycle_hors_statut, options_not_enforced
from . import validation_complete as vc


# ── La forme compacte ────────────────────────────────────────────────────────


def compacte(schema: Any) -> Any:
    """Le schéma réduit à ses clés de contrainte, sans clé nulle. `None` reste `None`."""
    if not isinstance(schema, dict):
        return schema
    return _garder(schema, "tete")


def _garder(noeud: dict, niveau: str) -> dict:
    """Même descente que `cles_inconnues.parcours` : on ne descend que par une clé que
    le niveau garde — un `lifecycle` sur un sous-champ n'a pas de niveau."""
    gardees = sk.CONTRAINTES[niveau]
    out: dict = {}
    for cle, v in noeud.items():
        if cle not in gardees or v is None:
            continue
        if cle == "fields" and isinstance(v, list):
            enfant = "champ" if niveau == "tete" else "sous_champ"
            v = [_garder(f, enfant) if isinstance(f, dict) else f for f in v]
        elif cle == "of" and isinstance(v, dict):
            v = _garder(v, "element")
        elif cle == "lifecycle" and isinstance(v, dict):
            v = _garder(v, "cycle")
        out[cle] = v
    return out


# ── Les gardes appliquées ────────────────────────────────────────────────────


def _chemin(pas: tuple) -> str:
    """`contacts.of.email` — les clés de colonne, et `of`/`lifecycle` entre elles."""
    if not pas:
        return "tête"
    return ".".join(str(p[1]) if p[0] == "fields" else p[0] for p in pas)


def _sans_effet(schema: Optional[dict]) -> list[dict]:
    out = [{"chemin": c, "cle": "agent_access", "raison": r}
           for c, r in aga.sans_effet(schema)]
    out += [{"chemin": c, "cle": "options",
             "raison": f"appliquée à partir du {vc.date_de_bascule().isoformat()} "
                       "(oto#124) : d'ici là, une valeur hors liste s'écrit, avec un "
                       "préavis"}
            for c in options_not_enforced(schema)]
    out += [{"chemin": c, "cle": "lifecycle",
             "raison": "seule la colonne de file voit ses transitions validées"}
            for c in lifecycle_hors_statut(schema)]
    for niveau, pas, noeud in cles_inconnues.parcours(schema):
        out += [{"chemin": _chemin(pas), "cle": c,
                 "raison": (_RETIRE if not pas and c == "unknown_columns" else
                            "aucun niveau ne l'admet : stockée, jamais lue")}
                for c in cles_inconnues.inconnues(niveau, noeud)]
    return out


#: oto#124 : le réglage retiré, encore STOCKÉ, est lu jusqu'à la date — puis plus.
_RETIRE = ("réglage retiré le 05/10/2026 (plus aucun réglage) : stocké, lu jusqu'à la "
           "date où le format fait contrat partout, puis retiré par la plateforme")


def gardes(schema: Optional[dict]) -> dict:
    """Ce que les gardes de colonne FONT sur ce tableau. Une liste vide est omise ;
    `{}` = rien de déclaré, rien d'inerte."""
    masq = aga.masquees(schema)
    out = {
        # Toute écriture, sur les deux faces ; seul qui possède ou gouverne force.
        "verrouillees": sorted(readonly_fields(schema)),
        # Face outil (MCP) seulement : la face REST sert l'écran du propriétaire.
        "masquees_a_l_agent": sorted(masq),
        "lecture_seule_agent": sorted(aga.fermees(schema) - masq),
        "sans_effet": _sans_effet(schema),
    }
    return {k: v for k, v in out.items() if v}
