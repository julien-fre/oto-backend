"""L'état d'un run tel qu'on le MONTRE — dérivé à la lecture, jamais écrit.

En production, 16 runs s'affichaient « en cours » ; **15 n'avaient plus donné signe
de vie depuis 1 jour à 1 mois** (#309, 12/08/2026). Ce ne sont pas des travaux en
cours : ce sont des conversations terminées sans que l'agent déclare la fin.
L'affichage mentait — dashboard, lentilles, et le bloc injecté que lisent tous les
agents.

Trois choix, et ils tiennent ensemble :

- **Dérivé, pas stocké.** Aucune colonne d'état, aucune tâche de fond : le silence
  se calcule en comparant le dernier signe de vie à maintenant. Une colonne écrite
  par un démon pourrait mentir à son tour — c'est le défaut qu'on ferme, on ne va pas
  le réintroduire un étage plus bas.
- **On SIGNALE, on ne relance pas.** Le silence est le régime dominant — **143 des
  148 runs ouverts** au 31/08/2026, silence médian **15 j 16 h**. Relancer une
  conversation que l'utilisateur a simplement fermée n'a aucun sens. On dit ce qu'on
  sait — « sans nouvelles depuis le … » — au lieu d'affirmer ce qu'on ignore.
- **Un seul endroit.** Toutes les surfaces (bloc injecté, lentilles, dashboard) lisent
  la même dérivation. Le mensonge d'origine venait déjà d'une règle recopiée : chaque
  surface décidait seule de ce que « pas d'issue » voulait dire.

## Le seuil, et la raison qu'il portait à tort (#666, 31/08/2026)

Jusqu'ici on lisait ici : « le recensement ne trouve **aucun** run silencieux entre
1 jour et 1 mois ». Cette phrase **inversait le recensement qu'elle citait** — #309 en
comptait 15 sur 16, c'est-à-dire exactement les runs que ce module existe pour nommer,
et le paragraphe d'ouverture le dit trois lignes plus haut. Le nombre était défendable,
sa justification était fausse ; et c'est le texte le plus proche du geste qui se relit
en premier quand on veut déplacer le seuil.

Deuxième recensement, 31/08/2026, **10 755 runs** (×126 en trois semaines), 148 ouverts :

- **143 des 148 ouverts** sont dans la bande que l'ancienne justification déclarait
  vide. Le creux existe, mais il est ailleurs : la tranche **6-24 h est vide** (0 run
  sur 148 ; 5 en deçà, 143 au-delà). Tout seuil posé dans ce creux rend donc le **même
  verdict** — on prend le plus grand des seuils sûrs.
- **Le risque de couper du vivant est identique à 24 h et à 48 h.** Sur les 10 722 runs
  d'au moins deux appels, le plus grand silence *interne* a une médiane de 1 min 43 et
  un p99,9 de 1 h 04 ; les **2** seuls runs qui dépassent 24 h sont les **2 mêmes** qui
  dépassent 48 h. À risque égal, 24 h nomme un run de plus.
- **L'asymétrie penche vers le bas.** `is_stale` est dérivé à la lecture : un faux
  « sans nouvelles » se corrige tout seul au premier appel suivant, alors qu'un « en
  cours » faux tient toute la durée du seuil.

⚠️ **Ce seuil nomme la pathologie, il ne la soigne pas — et la pathologie n'est pas
celle qu'on croit.** Le recensement l'attribue à la conversation : **18,3 %** des runs
conversationnels restent ouverts (106 sur 578) contre **0,41 %** côté runner hébergé
(42 sur 10 177, dont les jobs ont tous conclu). Mais découpé par CLIENT MCP le 31/08,
« la conversation » se scinde : `claude-code` laisse **5,9 %** de runs ouverts (26 sur
442), claude.ai 36 % (8 sur 22) — et **70,5 %** (67 sur 95) viennent d'un pilote de
flotte maison **qui n'émet plus rien depuis le 17/08**. Deux tiers des runs muets sont
donc une cohorte MORTE d'un client à nous, pas une habitude du modèle qu'une instruction
corrigerait. Instruction et remèdes chiffrés : #666.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

# Au-delà de ce silence, un run ouvert cesse d'être annoncé « en cours ».
# 24 h depuis le 31/08/2026 (#666) — la justification est en tête de module, et elle
# n'est pas « 48 h était trop haut » mais « à risque de faux positif ÉGAL, le seuil
# bas nomme davantage ».
STALE_AFTER = timedelta(hours=24)

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
