"""Domicile unique de l'INSTRUCTION DE DÉPART servie à un agent hébergé.

⚠️ Tranché le 02/09/2026, redit le 03 : **le worker ne compose pas**. Il est un
client MCP qui exécute une instruction — il ne sait pas ce qu'elle contient, et
n'a rien à y ajouter. Une instruction par défaut vivant dans le runner
(`DEFAULT_INPUT`, retiré) faisait l'inverse : elle inventait le travail à la
place de qui l'avait déclaré, et le faisait dans le seul étage qui ne connaît
pas le métier — donc sans jamais pouvoir être ni relue, ni corrigée, ni même vue
depuis le produit.

Composer revient ICI, côté backend, qui sait ce qu'est une procédure et ce
qu'est une file. Et ce qu'on compose reste MINIME : une instruction rédigée à la
main est un **second domicile du métier** — la même règle vit dans la procédure
ET dans l'instruction, et l'une des deux finit par mentir. Une instruction qui
POINTE l'objet ne peut pas diverger de lui.

Les deux surfaces qui déclarent un agent (déclencheur, flotte) passent par ici :
une seule d'entre elles qui rédigerait sa propre variante rouvrirait le second
domicile à l'échelle du dépôt.
"""
from __future__ import annotations

import json
from typing import Any, Optional

_BILAN = ("conclus par un bilan bref de ce que tu as fait et de ce qui t'a "
          "manqué.")


def derivee(slug: str) -> str:
    """L'instruction MINIMALE : « lis l'objet, applique-le »."""
    return (f"Lis la procédure `{slug}` et applique-la. Elle fait autorité : "
            f"n'invente rien qu'elle ne dise, et {_BILAN}")


def de_file(slug: str, namespace: Optional[str],
            row_filter: Optional[dict[str, Any]] = None) -> str:
    """La même, plus la MÉCANIQUE DE FILE — qui n'est pas du métier.

    « Réserve une ligne, une seule, rends-la » appartient à la flotte, pas à la
    procédure : c'est la plateforme qui distribue le travail entre plusieurs
    agents. L'écrire ici, et non dans la procédure, garde chaque règle chez qui
    la porte — et évite qu'un client recopie à la main, dans chaque campagne, un
    protocole que la plateforme est seule à savoir juste.

    Sans cible déclarée, il n'y a pas de file à décrire : on rend l'instruction
    nue plutôt qu'un protocole qui désignerait un tableau imaginaire.
    """
    if not namespace:
        return derivee(slug)
    filtre = (f", filtre `{json.dumps(row_filter, ensure_ascii=False)}`"
              if row_filter else "")
    return (
        f"Lis la procédure `{slug}` et applique-la : elle fait autorité, "
        "n'invente rien qu'elle ne dise.\n"
        f"Ta file de travail est le tableau `{namespace}`{filtre}. Réserve UNE "
        "SEULE ligne avec `data_claim_next` — jamais une deuxième, un autre "
        "agent prendra la suivante. Si la réservation ne rend rien, la file est "
        "vide : conclus et arrête-toi.\n"
        "Écris ton résultat en un seul appel sur `@claimed`, relis-le, puis "
        f"{_BILAN}")
