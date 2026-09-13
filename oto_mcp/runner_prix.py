"""Le PRIX d'un jeton — et pourquoi un barème se DATE.

Un relevé de coût qu'on garde POUR TOUJOURS ne peut pas se tarifer à la lecture.
Le jour où un fournisseur change ses prix, tarifer à la lecture réécrirait le
passé : un déroulé de février se mettrait à coûter le tarif de mars, et le total
d'un mois déjà facturé changerait tout seul. **Le prix se fige à l'écriture**, et
la ligne garde le nom du barème qui l'a produite — sans quoi on ne peut ni
expliquer un montant ni le recalculer.

D'où la forme : une suite de BARÈMES datés, et non une table de prix. Le barème
en vigueur est le plus récent dont la date d'effet précède le travail.

⚠️ **Ce fichier ne sait rien de la facturation.** Il convertit des jetons en
dollars, point. La marge, les remises, les crédits et ce qu'on refacture à un
client vivent dans Tulina (`usage/`), jamais ici : ce dépôt est public et ne
porte que des lectures neutres.

⚠️ **Un modèle inconnu ne vaut pas ZÉRO.** `cout_nano_usd` rend `None`, et la
colonne reste NULL. Un zéro se lirait « ce déroulé n'a rien coûté », ce qui est
faux et se propage dans toutes les sommes ; un NULL se lit « non tarifé » et
oblige l'écran à le dire.

Pur : aucune dépendance, pour que la base comme les capacités puissent le lire —
même contrainte que `runner_models`.
"""
from __future__ import annotations

import datetime
from typing import NamedTuple, Optional


class Prix(NamedTuple):
    """Les quatre postes, en NANO-DOLLARS par jeton (1 nUSD = 10⁻⁹ USD).

    Pourquoi le nano-dollar : à cette échelle, tout prix publié tombe sur un
    ENTIER. Un jeton de sortie d'Opus à 25 $/MJetons vaut exactement 25 000 ; une
    lecture de cache à 0,50 $/MJetons vaut exactement 500. Des flottants dans une
    colonne qu'on somme des millions de fois dérivent ; des entiers, non.
    """
    entree: int
    sortie: int
    ecriture_cache: int
    lecture_cache: int


def _tarif(entree: int, sortie: int) -> Prix:
    """Les quatre postes DÉRIVÉS des deux publiés.

    Les fournisseurs publient deux prix (entrée, sortie) et expriment le cache en
    multiples de l'entrée : écrire coûte 1,25×, lire coûte 0,1×. Les dériver plutôt
    que de saisir quatre nombres par modèle rend un changement de prix ponctuel —
    une ligne — au lieu de quatre occasions de se tromper.
    """
    return Prix(entree, sortie, entree * 5 // 4, entree // 10)


#: Les barèmes, du plus ancien au plus récent. Chacun est daté de son ENTRÉE EN
#: VIGUEUR, et un travail est tarifé au barème en vigueur le jour où il finit.
#:
#: ⚠️ Le premier barème ne prétend PAS valoir pour le passé : il vaut depuis le
#: jour où ce lot est déployé. Les prix d'avant ne sont pas connus de ce dépôt, et
#: les inventer pour tarifer rétroactivement produirait des montants faux avec
#: l'apparence de l'exactitude. Ajouter un barème = ajouter une entrée ici, jamais
#: modifier une entrée existante — une ligne déjà écrite garde son montant.
BAREMES: tuple[tuple[str, dict[str, Prix]], ...] = (
    ("2026-09-13", {
        # Anthropic, tarif public de l'API première partie (relevé le 24/06/2026).
        # ⚠️ Bedrock et Vertex sont tarifés séparément : un worker servi par eux
        # serait tarifé faux ici. Aucun ne l'est aujourd'hui.
        "claude-opus-5": _tarif(5_000, 25_000),
        "claude-sonnet-5": _tarif(2_000, 10_000),
        "claude-haiku-4-5": _tarif(1_000, 5_000),
        # Mistral, tarif public « Mistral Large » (mistral.ai/pricing, 13/09/2026) :
        # 0,50 $ / 1,50 $ par million de jetons.
        # ⚠️ La page ne décline PAS le prix par version — celui-ci est le tarif de
        # la gamme, pas celui de `2512` nommément. À confirmer avant de s'appuyer
        # dessus pour refacturer.
        # ⚠️ Les postes de cache sont dérivés par symétrie et ne sont jamais
        # employés : la voie OpenAI-compatible ne rend que `prompt_tokens` et
        # `completion_tokens`, donc les colonnes de cache restent à zéro.
        "mistral-large-2512": _tarif(500, 1_500),
    }),
)

#: Le barème le plus récent — ce qu'un écran affiche comme « tarif courant ».
BAREME_COURANT = BAREMES[-1][0]


def bareme_en_vigueur(le: Optional[datetime.date] = None) -> tuple[str, dict[str, Prix]]:
    """Le barème applicable à cette date (défaut : aujourd'hui).

    Un travail antérieur au premier barème est tarifé AU PREMIER : c'est le seul
    choix honnête possible — on n'a pas les prix d'avant, et refuser de tarifer
    rendrait NULL tout l'historique repris. Le nom du barème voyage avec la ligne,
    donc l'approximation reste lisible plutôt que cachée.
    """
    jour = (le or datetime.date.today()).isoformat()
    retenu = BAREMES[0]
    for entree in BAREMES:
        if entree[0] <= jour:
            retenu = entree
    return retenu[0], retenu[1]


def cout_nano_usd(modele: Optional[str], *, entree: Optional[int] = 0,
                  sortie: Optional[int] = 0, ecriture_cache: Optional[int] = 0,
                  lecture_cache: Optional[int] = 0,
                  le: Optional[datetime.date] = None
                  ) -> tuple[Optional[int], Optional[str]]:
    """`(montant en nano-dollars, nom du barème)` — ou `(None, None)`.

    Rend `None` quand le modèle est inconnu du barème, ou quand AUCUN compte de
    jetons n'a été mesuré (un travail mort avant d'avoir rien rendu). Dans les
    deux cas, la ligne reste NULL et la somme qui la contient se déclare
    incomplète : c'est la différence entre « n'a rien coûté » et « on ne sait pas ».
    """
    nom, table = bareme_en_vigueur(le)
    prix = table.get(modele or "")
    if prix is None:
        return None, None
    postes = (entree, sortie, ecriture_cache, lecture_cache)
    if all(p is None for p in postes):
        return None, None
    return (int(entree or 0) * prix.entree
            + int(sortie or 0) * prix.sortie
            + int(ecriture_cache or 0) * prix.ecriture_cache
            + int(lecture_cache or 0) * prix.lecture_cache), nom


def en_dollars(nano_usd: Optional[int]) -> Optional[float]:
    """Le montant en dollars, pour un écran. ⚠️ À l'AFFICHAGE seulement : tout ce
    qui se somme, se compare ou se facture reste en entiers nano-dollars."""
    return None if nano_usd is None else nano_usd / 1e9
