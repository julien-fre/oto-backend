"""Écarter la VALEUR fautive plutôt que la fiche entière (#667).

Une écriture refusée par le schéma l'était en bloc : une seule sous-valeur hors
des options déclarées, et tout l'appel repartait. Mesuré le 02/09/2026 sur une
vague de 40 écritures d'agents : **8 rejets, dont 5 pour ce seul motif**, chacun
emportant une fiche entière — effectif relevé au registre, convention collective
vérifiée, interlocuteurs trouvés, qualification rédigée et sourcée. Environ
60 000 jetons par fiche, déjà payés, à repayer.

⚠️ **Le refus reste légitime, c'est sa PORTÉE qui ne l'est pas.** La colonne du
cas mesuré déclare `__non_conserve__` : le client exige que le profil
professionnel d'une personne physique ne soit pas conservé. Cette valeur-là ne
doit surtout pas s'écrire. Mais l'agent qui l'y range ne commet pas une faute de
STRUCTURE — il a trouvé une donnée publique et l'a mise dans un champ que le
schéma ferme. *Le verrou doit protéger la donnée, pas détruire le reste.*

## Ce qui s'écarte, et ce qui continue de tout refuser

**Seules les valeurs hors options.** Les deux autres refus de la même vague sont
de vraies incohérences d'enregistrement, et leur rejet total est juste : une
réservation ambiguë (la plateforme ne peut pas deviner où écrire) et un champ
requis manquant (la fiche serait fausse). Le partage passe donc là : une règle
violée sur une VALEUR isolée s'écarte, une règle violée sur la COHÉRENCE de la
ligne refuse tout.

⚠️ **Et l'écartement se REVALIDE.** Retirer une valeur peut en défaire une autre
— une colonne-aiguillage écartée cesse de rendre requis ce qu'elle gardait, et
la ligne écrite serait incomplète sans que rien ne le dise. La ligne amputée
repasse donc la validation entière : propre, elle s'écrit ; fautive, l'appel
retombe sur le refus d'origine, message compris. Aucune ligne n'est écrite sur
la foi d'un contrôle qui n'a pas vu sa forme finale.

⚠️ **Pas de réglage par tableau** (demande explicite du signal) : ce serait un
cran de plus à tenir, et un défaut qui ne se voit qu'en production quand il est
mal posé. Le comportement est le bon par défaut, pour tout le monde.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# `contacts[0].linkedin` → [("contacts", 0), ("linkedin", None)]. Le format est
# CELUI QU'ON PRODUIT (`_row_errors` compose `path`), pas une phrase reparsée :
# la distinction compte, un message français ne se relit jamais comme un contrat.
_SEGMENT = re.compile(r"^([^.\[\]]+)((?:\[\d+\])*)$")
_INDICE = re.compile(r"\[(\d+)\]")


def _segments(champ: str) -> Optional[list]:
    """Le chemin, découpé — ou None s'il ne se lit pas (on n'écarte alors rien)."""
    out: list = []
    for brut in champ.split("."):
        m = _SEGMENT.match(brut)
        if not m:
            return None
        out.append((m.group(1), [int(i) for i in _INDICE.findall(m.group(2))]))
    return out


def tete(champ: str) -> Optional[str]:
    """La colonne de PREMIER NIVEAU d'un chemin — celle que `written` nomme."""
    seg = _segments(champ)
    return seg[0][0] if seg else None


def retirer(data: dict, champ: str) -> bool:
    """Retire `champ` de `data`, en place. Rend True si quelque chose a disparu.

    ⚠️ Retire la clé ENTIÈRE, couches comprises : une valeur interdite dont on ne
    garderait que la provenance laisserait la trace de ce que le tableau refuse
    de conserver — c'est-à-dire l'inverse de ce que le cran protège."""
    seg = _segments(champ)
    if not seg:
        return False
    courant: Any = data
    for cle, indices in seg[:-1]:
        if not isinstance(courant, dict) or cle not in courant:
            return False
        courant = courant[cle]
        for i in indices:
            if not isinstance(courant, list) or i >= len(courant):
                return False
            courant = courant[i]
    cle, indices = seg[-1]
    if indices:
        # `contacts[0]` en position finale : on vide l'ATTRIBUT, pas l'élément —
        # sauf qu'ici le dernier segment porte la clé, donc l'élément visé est le
        # conteneur. Ce cas n'est pas produit par la validation (un item de liste
        # fautif nomme toujours un attribut) ; on ne devine pas.
        return False
    if not isinstance(courant, dict) or cle not in courant:
        return False
    del courant[cle]
    return True


def rapport(ecartes: list) -> dict:
    """La 6ᵉ clé du relevé d'écriture : ce que le geste n'a PAS écrit, en ayant
    écrit le reste. Vide quand rien n'est écarté — pas de clé parasite.

    Distincte de `hors_options`, et la nuance est tout le sujet : là, une valeur
    hors d'une liste que rien ne fait respecter, ÉCRITE quand même ; ici, une
    valeur qu'un schéma armé refuse, PAS écrite, le reste de la ligne l'ayant
    été. Les confondre ferait croire à une donnée en base qui n'y est pas."""
    if not ecartes:
        return {}
    return {
        "valeurs_ecartees": ecartes,
        "valeurs_ecartees_hint": (
            f"{len(ecartes)} valeur(s) refusée(s) par le schéma ont été ÉCARTÉES ; "
            "le reste de la ligne est écrit. Corrige-les et réécris CE champ seul — "
            "il est inutile de refaire la ligne."),
    }
