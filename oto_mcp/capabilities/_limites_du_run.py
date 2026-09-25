"""Les limites d'UN run, déclarées par l'utilisateur sur son agent (déclencheur ou flotte).

- `max_tokens` : ce qu'un run peut consommer, en JETONS — jamais en monnaie (règle de
  `runner_fleets`, schéma `runs.py` : un tarif figé en base devient faux sans que rien
  ne le dise). L'écran en montre l'équivalent au tarif du jour ; l'agent s'arrête dessus
  (`stopped=max_tokens`). Sur une flotte, c'est `max_tokens_per_row`, qui existait déjà.
- `max_run_seconds` : la durée murale d'un run. Au-delà, l'exécuteur l'arrête.

Aucune n'a de défaut ICI, comme `max_steps` : NULL = rien ne part avec le travail, et
l'exécuteur garde exactement ce qu'il avait (la boucle ordinaire n'a aucune échéance
murale ; le chemin one-shot garde ses 900 s ; pas de plafond de jetons). Poser un
défaut dans le backend fabriquerait une borne que personne n'a déclarée.

`0` est le geste pour RETIRER une limite posée (`update` ne distingue pas une absence
d'un NULL) : il s'écrit NULL, jamais 0.
"""
from __future__ import annotations

from typing import Optional

from ._types import AuthzDenied

#: Une minute : en deçà, un run n'a pas le temps d'ouvrir son contexte.
DUREE_MIN_S = 60
#: Une heure : ce que la ferme tient (`RuntimeMaxSec`, un run à la fois par sandbox).
#: Au-delà, c'est une question de capacité, pas un réglage.
DUREE_MAX_S = 3600


def valider(max_tokens: Optional[int], max_run_seconds: Optional[int]) -> None:
    if max_tokens is not None and max_tokens < 0:
        raise AuthzDenied(400, "invalid_bound",
                          f"`max_tokens`={max_tokens} : un plafond se compte (≥ 1), "
                          "et `0` le retire.")
    if max_run_seconds is not None and max_run_seconds != 0 and not (
            DUREE_MIN_S <= max_run_seconds <= DUREE_MAX_S):
        raise AuthzDenied(400, "invalid_bound",
                          f"`max_run_seconds`={max_run_seconds} : entre {DUREE_MIN_S} et "
                          f"{DUREE_MAX_S} s, ou `0` pour revenir au défaut de l'exécuteur.")


def a_ecrire(valeur: Optional[int]) -> Optional[int]:
    """Ce que la colonne reçoit : `0` retire la limite (NULL)."""
    return None if valeur == 0 else valeur


def charge(max_tokens: Optional[int], max_run_seconds: Optional[int]) -> dict:
    """Ce qui part avec le travail — seulement ce qui est déclaré."""
    out = {}
    if max_tokens:
        out["max_tokens"] = max_tokens
    if max_run_seconds:
        out["max_seconds"] = max_run_seconds
    return out
