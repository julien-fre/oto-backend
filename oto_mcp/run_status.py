"""L'état d'un run tel qu'on le MONTRE — dérivé à la lecture, jamais écrit.

En production, 16 runs s'affichaient « en cours » ; **15 n'avaient plus donné signe
de vie depuis 1 jour à 1 mois**. Ce ne sont pas des travaux en cours : ce sont des
conversations terminées sans que l'agent déclare la fin. L'affichage mentait —
dashboard, lentilles, et le bloc injecté que lisent tous les agents.

Trois choix, et ils tiennent ensemble :

- **Dérivé, pas stocké.** Aucune colonne d'état, aucune tâche de fond : le silence
  se calcule en comparant le dernier signe de vie à maintenant. Une colonne écrite
  par un démon pourrait mentir à son tour — c'est le défaut qu'on ferme, on ne va pas
  le réintroduire un étage plus bas.
- **On SIGNALE, on ne relance pas.** Le silence est le régime dominant (94 % des runs
  ouverts) : relancer une conversation fermée n'a aucun sens. On dit ce qu'on sait —
  « sans nouvelles depuis le … » — au lieu d'affirmer ce qu'on ignore.
- **Un seul endroit.** Toutes les surfaces (bloc injecté, lentilles, dashboard) lisent
  la même dérivation. Le mensonge d'origine venait déjà d'une règle recopiée : chaque
  surface décidait seule de ce que « pas d'issue » voulait dire.

Le seuil vient de la mesure, pas d'un avis (oto-backend#309) : le recensement ne
trouve **aucun** run silencieux entre 1 jour et 1 mois. La coupure est nette, donc
48 h ne marque aucune population à tort.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

# Au-delà de ce silence, un run ouvert cesse d'être annoncé « en cours ».
STALE_AFTER = timedelta(hours=48)

# Issues qu'un agent peut déclarer — le vocabulaire d'ADR 0058-D5, moins ce qui n'y
# figure pas.
#
# `abandoned` est retiré (13/08) : il n'apparaît pas dans D5, et sur toute l'histoire
# du journal aucun des 8 auteurs ne l'a employé (#309). Un mot que personne n'utilise
# n'est pas neutre — il fait hésiter au moment de clore, et une clôture qui hésite est
# une clôture qui n'arrive pas. C'est une des fabriques du run muet.
#
# ⚠️ `failed` reste, bien qu'il n'ait jamais servi non plus : **D5 le porte**. La
# mesure dit qu'un retrait serait indolore, pas qu'il serait juste — on ne retire pas
# du vocabulaire d'une décision d'architecture au motif qu'il n'a pas encore servi.
# Un run peut échouer sans être bloqué, et l'absence de ce cas dans l'histoire dit
# surtout que la plateforme est jeune.
#
# `stale` n'est PAS ici : il ne se déclare pas, il se dérive (cf. `is_stale`).
OUTCOMES = ("done", "failed", "blocked")


def _as_aware(value: Any) -> Optional[datetime]:
    """Les lectures de ce dépôt rendent parfois les dates en CHAÎNE (le row factory
    normalise pour les réponses JSON). On accepte les deux plutôt que d'imposer une
    forme aux appelants — sinon la dérivation marcherait sur une surface et pas sur
    l'autre, ce qui est très exactement le défaut qu'on corrige."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def is_stale(outcome: Any, last_seen_at: Any, *, now: Optional[datetime] = None) -> bool:
    """Un run OUVERT dont plus rien n'est arrivé depuis le seuil.

    Un run clos ne l'est jamais : son issue est un fait déclaré, elle prime sur toute
    inférence. Et sans dernier signe de vie lisible, on répond `False` — l'ignorance
    ne doit pas se transformer en affirmation.
    """
    if outcome:
        return False
    seen = _as_aware(last_seen_at)
    if seen is None:
        return False
    return (now or datetime.now(timezone.utc)) - seen > STALE_AFTER


def describe(run: Mapping, *, now: Optional[datetime] = None) -> str:
    """Ce qu'on écrit à côté d'un run — la formule est la même partout.

    Un run clos porte son issue. Un run silencieux porte la DATE de son dernier signe
    de vie, pas une durée : « sans nouvelles depuis le 3 août » se vérifie d'un coup
    d'œil dans le journal, « depuis 10 jours » oblige à compter.
    """
    outcome = run.get("outcome")
    if outcome:
        return f"→ {outcome}"
    seen = _as_aware(run.get("last_seen_at"))
    if seen is not None and is_stale(outcome, seen, now=now):
        return f"(sans nouvelles depuis le {seen:%d/%m})"
    return "(en cours)"
