"""Une valeur sort-elle d'une liste DÉCLARÉE ? — la seule réponse, pour tous (#98).

Trois lecteurs posent la même question et doivent recevoir la même réponse :

- le REFUS du régime strict (`validation._hors_options`) ;
- le SIGNALEMENT du régime souple (`non_applique.unenforced_options`) ;
- le relevé SQL de l'existant à la pose (`db.datastore_offending_enum_values`, qui
  compare la valeur lue par `db.paths.field_value_sql`, jumelle d'`unwrap`).

Jusqu'au 10/09/2026, les deux premiers ne la posaient que sur `enum`, et chacun avec
sa formule (`value not in allowed` d'un côté, `str(v) not in opts` de l'autre). Sur les
dix autres types, `options` était acceptée à la pose et n'engageait rien, tableau
strict compris : ni refus, ni signalement. Une règle écrite deux fois finit par dire
« conforme » d'un côté et « hors liste » de l'autre ; ici elle l'est une fois.
"""
from __future__ import annotations

import json
from typing import Any, Optional


def valeur_comparee(value: Any) -> Optional[str]:
    """La valeur sous la forme où la BASE la compare — ce que rend `data->>champ` —,
    ou `None` pour un composite, qui n'est jamais une option.

    D'où `true`/`false` pour un booléen (et non `True`), et `3.0` pour un flottant :
    exactement ce que Postgres rend pour ces JSON. Le relevé SQL de la pose et le
    jugement Python d'une écriture voient ainsi la même chose."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return None


def hors_des_options(value: Any, options: Optional[list]) -> bool:
    """La valeur sort-elle de la liste ? Une liste vide ne condamne rien (enum libre).

    La valeur doit arriver DÉBALLÉE (`couches.unwrap`) : c'est elle qu'on juge, pas
    son enveloppe. Le vide n'arrive jamais ici — c'est l'affaire de `required`, et
    chaque appelant l'écarte avant de juger."""
    if not options:
        return False
    texte = valeur_comparee(value)
    return texte is None or texte not in [str(o) for o in options]


def montrable(value: Any) -> str:
    """La valeur telle qu'on la CITE dans un message : sa forme comparée, ou son JSON
    pour un composite — jamais le repr d'un dict Python, que personne n'a écrit."""
    texte = valeur_comparee(value)
    return texte if texte is not None else json.dumps(value, ensure_ascii=False)
