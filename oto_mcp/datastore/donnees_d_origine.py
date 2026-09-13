"""`donnees_d_origine=True` : cet appel apporte la donnée telle qu'elle a été REMISE.

Un import n'est pas une mécanique à part — c'est écrire des lignes. Ce qui le
distingue d'une écriture ordinaire, ce n'est ni le volume, ni le format, ni le chemin :
**c'est qu'il apporte de la donnée d'origine**. Partout ailleurs une écriture vise la
version courante et n'a pas à le dire.

Ce paramètre rend cette distinction EXPLICITE. Avant lui, elle dépendait d'un ordre
d'opérations auquel personne ne pensait au bon moment — le cran `origine: "system"`
devait avoir été déclaré AVANT que la ligne n'existe, sans quoi la capture paresseuse
n'avait plus rien à figer. ⚠️ **Ce silence a coûté 837 cellules sur 846** sur un tableau
de campagne : le cran déclaré après coup, les valeurs de la cliente déjà écrasées par
des agents, et un balayage qui n'a pu poser qu'un aveu — `(origine inconnue)`.

Un geste explicite ne se trompe pas d'ordre. Il n'y a plus « avant » ni « après » :
l'appel dit ce qu'il apporte, et la version d'origine se pose au moment où la valeur
entre, parce que c'est le même geste.

## Les deux versions sont écrites, pas seulement l'origine

⚠️ **Mesuré avant d'être choisi, et c'est l'inverse de ce qui semblait économique.**
Une case qui ne porterait QUE son origine se lit `None` — la règle est dans `unwrap`
(« pas de `valeur`, mais que des couches connues ⟹ la valeur n'est pas encore posée »),
et son JUMEAU SQL (`db/paths.py`, aligné sur elle par oto#163) la tient pour les
filtres, le tri, les agrégats et les contrôles de schéma. Les lignes d'un import
paraîtraient donc vides, sauf à modifier cette règle dans deux langages pour économiser
du stockage.

Donc les deux versions sont écrites, avec les mêmes couches. **Ce n'est pas une
redondance : ce sont deux faits distincts qui se trouvent égaux le premier jour.** L'un
bougera au premier enrichissement, l'autre jamais.

## Trois règles, chacune fermant une porte

- **une origine DÉJÀ posée n'est jamais touchée.** Elle est figée par nature — c'est
  ce qui la rend fiable, et c'est pourquoi elle n'a pas besoin d'être déclarée
  `readonly`. Un ré-import ne l'écrase donc pas : une ligne retrouvée par sa clé
  métier est une mise à jour, la version courante bouge, l'origine non ;
- **une colonne VIDE ne reçoit rien.** Demandé par la campagne, et juste sur le fond :
  poser une origine sur une case que la cliente n'a pas remplie affirmerait qu'elle a
  remis du vide. Elle n'a rien remis, ce qui n'est pas la même chose ;
- **les couches de l'appel vont dans les DEUX versions.** À l'import, `comment` décrit
  d'où vient la donnée — c'est vrai du courant comme de l'origine, puisqu'ils sont la
  même valeur ce jour-là. Ne le poser que sur l'origine ferait disparaître le
  commentaire de la lecture par défaut, qui sert la version courante.
"""
from __future__ import annotations

from typing import Any, Optional

from . import schema as dsv2

#: Le nom servi. Il dit ce que l'appel APPORTE, jamais quelle case il vise — et il ne
#: réutilise pas `versions`, qui est le paramètre de LECTURE (« ce que je veux
#: recevoir »). Un mot, un sens : c'est la leçon du `datastore`, qui désignait deux
#: choses dont une seule existait encore.
PARAMETRE = "donnees_d_origine"


def _couches_de(colonne: Any) -> dict:
    """Les couches écrites pour cette colonne, valeur comprise, sous forme de dict.

    Un scalaire est une valeur sans couches. On ne juge pas ici ce qui est licite :
    les gardes de champs réservés et de schéma ont déjà parlé quand on arrive.
    """
    if isinstance(colonne, dict) and (
            dsv2.VALUE_LAYER in colonne
            or (colonne and all(k in dsv2.ALL_LAYER_KEYS for k in colonne))):
        return dict(colonne)
    return {dsv2.VALUE_LAYER: colonne}


def _vide(valeur: Any) -> bool:
    """Rien n'a été remis. `0` et `False` sont des valeurs remises, pas du vide —
    les confondre effacerait l'origine d'un effectif nul ou d'un booléen faux."""
    return valeur is None or (isinstance(valeur, str) and valeur.strip() == "")


def poser_les_deux_versions(user_data: dict, avant: Optional[dict] = None,
                            schema: Optional[dict] = None) -> list[str]:
    """Fige la version d'origine de chaque colonne apportée. `user_data` est modifiée
    en place ; rend les colonnes sur lesquelles une origine vient d'être posée.

    `avant` = la ligne déjà en base pour une mise à jour (ré-import retrouvé par sa
    clé métier), `None` pour une création. Il sert à une seule chose : ne pas toucher
    une origine déjà posée.

    ⚠️ **`schema` n'écarte plus rien — `origine: "system"` est SUPPRIMÉ (08/09/2026).**
    Ce paramètre est conservé pour ne pas casser les appelants ; il n'a plus d'effet.

    L'histoire vaut d'être gardée : quand ce geste a été livré, il entrait en collision
    avec le cran, qui interdisait à quiconque d'écrire la couche d'origine. J'ai résolu
    le conflit en écartant les colonnes à cran — donc en donnant la priorité à l'ancien
    mécanisme sur le nouveau, et en l'écrivant « celui qui était là d'abord garde la
    main ». C'était l'inverse de la décision produit, et je l'ai justifié proprement,
    ce qui est le pire. **Un conflit entre l'ancien et le nouveau ne se résout pas en
    pérennisant l'ancien.**
    """
    posees: list[str] = []
    for cle, colonne in list(user_data.items()):
        couches = _couches_de(colonne)
        if _vide(couches.get(dsv2.VALUE_LAYER)):
            continue
        if dsv2.ORIGIN_LAYER in couches:
            # L'appelant a écrit l'origine lui-même : c'est le chemin déclaré
            # (`origine_override`), avec ses propres gardes. On ne se superpose pas.
            continue
        deja = dsv2.layer_value((avant or {}).get(cle), dsv2.ORIGIN_LAYER)
        if deja is not None:
            continue
        # La version d'origine porte les MÊMES couches que ce qui est apporté —
        # sans `origine`, car il n'y a pas d'origine d'une origine.
        couches[dsv2.ORIGIN_LAYER] = {k: v for k, v in couches.items()
                                      if k != dsv2.ORIGIN_LAYER}
        user_data[cle] = couches
        posees.append(cle)
    return posees


def description_parametre(en: bool = False) -> str:
    """Ce que les DEUX faces disent du paramètre, dans leur description servie.

    ⚠️ Une seule fonction, pas deux textes : la face REST et la face MCP décriraient
    sinon le même paramètre en deux termes, et l'écart se lirait comme deux paramètres
    différents. C'est la règle déjà payée sur `description_parametre_origine`.

    ⚠️ La face MCP ne peut pas COMPOSER sa description — `@mcp.tool()` lit la docstring
    littérale du handler, et `description=` emporterait les descriptions d'arguments.
    Le texte anglais y est donc RECOPIÉ, et un banc exige qu'il porte au moins le nom
    du paramètre : la copie est surveillée, faute de pouvoir être évitée.

    Ce que le texte DOIT dire, et pourquoi chaque morceau y est :

    - **où mettre la provenance** — dans `<champ>.comment`, pas dans un paramètre à
      part. Une deuxième façon d'écrire un commentaire serait un mécanisme de plus
      pour le même résultat ;
    - **import, pas enrichissement** — c'est la seule erreur qui coûte cher : marquer
      d'origine ce qu'un agent a établi présenterait son travail comme la donnée de
      la cliente, exactement ce que la définition interdit ;
    - **une origine posée ne se réécrit pas** — sinon un ré-import détruirait ce que
      le premier a figé, et personne ne le verrait ;
    - **une case vide ne reçoit rien** — « la cliente n'a rien remis » et « la cliente
      a remis du vide » sont deux faits différents, et l'écart se lit à la restitution.
    """
    if en:
        return (f"`{PARAMETRE}=true` states that this call brings data AS THE CLIENT "
                f"HANDED IT OVER — an import. Each cell gets its `origine` version "
                f"frozen at the same time as its current value, carrying the same "
                f"layers: put the provenance in `<field>.comment` and it lands in "
                f"both. Use it for the import itself, NOT for enrichment — what an "
                f"agent establishes is the current version. An origin already set is "
                f"never overwritten (a re-import updates the current version and "
                f"leaves the origin alone), and an empty cell gets nothing: the "
                f"client handed over nothing there, which is not the same as handing "
                f"over an empty value.")
    return (f"`{PARAMETRE}=true` déclare que cet appel apporte la donnée TELLE QUE LA "
            f"CLIENTE L'A REMISE — un import. Chaque case reçoit sa version "
            f"`origine`, figée en même temps que sa valeur courante et portant les "
            f"mêmes couches : mets donc la provenance dans `<champ>.comment`, elle "
            f"sera dans les deux. À réserver à l'IMPORT, pas à l'enrichissement — ce "
            f"qu'un agent établit est la version courante. Une origine déjà posée "
            f"n'est jamais réécrite (un ré-import met à jour le courant et laisse "
            f"l'origine), et une case vide ne reçoit rien : la cliente n'y a rien "
            f"remis, ce qui n'est pas la même chose que remettre du vide.")
