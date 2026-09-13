"""`null` cesse de vouloir dire « efface » (oto#140) — préavis, puis refus.

Dans le datastore, `{"champ": null}` EFFACE la valeur. Partout ailleurs — dans un
schéma, dans une réponse, dans le JSON de n'importe qui — `null` veut dire « pas de
valeur ». **Le même jeton dit donc une chose et son contraire selon l'endroit**, et
c'est le genre d'ambiguïté qui ne se paie pas à l'écriture mais trois semaines plus
tard, sur une donnée absente que personne n'a voulu détruire.

Le contrat le remplace par des gestes qui se distinguent :

| ce que l'agent envoie | ce que ça veut dire |
|---|---|
| `@clear` | je l'efface, sans rien affirmer (oto#204) |
| `@empty` | le vide est ASSUMÉ : aucune source ne donne cette valeur (oto#204) |
| l'omission (ou `@keep`) | je n'y touche pas |

## Pourquoi un préavis, et pas un retrait

⚠️ **Mesuré chez le plus gros écrivain de la plateforme : 1 862 `"valeur": null` dans
ses journaux d'appels**, plus des centaines de scalaires nus (`groupe_siren`,
`chiffre_affaires`, `qualification_piece`…). Et surtout : **le sens que ses agents lui
donnent est « cherché, rien trouvé »** — l'exact opposé de « efface ». Un de leurs
textes servis porte même l'exemple `{"facebook": {"valeur": null}}`.

Retirer `null` sans préavis casserait donc leurs six procédures le jour où elles
tournent, et le message d'erreur arriverait à un agent qui ne peut pas republier sa
propre procédure. Le préavis existe pour que le geste change chez celui qui écrit les
procédures, pas chez celui qui les exécute.

## Ce que le refus devra dire, quand il tombera

**Nommer les deux gestes, jamais interpréter en silence.** Un `null` traduit d'office
« pour rendre service » se tromperait dans les deux sens : en `@clear`, il effacerait
une valeur chez un agent qui voulait dire « rien trouvé » — exactement le dégât que ce
lot existe pour empêcher, commis par la correction elle-même ; en `@empty`, il
affirmerait un vide que l'agent n'a peut-être jamais cherché (oto#204). Le refus dit
`@clear` pour effacer et `@empty` pour un vide assumé, et l'agent choisit. Demandé
explicitement par la campagne, et c'est la bonne demande.
"""
from __future__ import annotations

from datetime import date as _date, datetime as _datetime, timezone as _timezone
from typing import Any, Optional

from . import couches as dsl

#: La date à partir de laquelle `null` est REFUSÉ.
#:
#: Pourquoi le 1er décembre 2026 (arbitré le 08/09/2026) : le plus gros écrivain doit
#: republier six procédures, et il a annoncé le faire après la revue cliente en cours.
#: Trois mois couvrent cette refonte et un trimestre d'intégration pour les tiers qu'on
#: ne connaît pas. La date vit dans le CODE, pas dans l'env d'une box : ce que le tronc
#: annonce doit être exactement ce qu'il refusera.
NULL_REFUSE_LE = _date(2026, 12, 1)

#: Déplace la date sans déployer (`YYYY-MM-DD`). Une valeur illisible LÈVE plutôt que
#: de retomber sur le défaut — un préavis dont la date est muette annonce une échéance
#: que rien n'applique.
ENV_NULL_REFUSE_LE = "OTO_NULL_REFUSE_LE"


def date_refus() -> _date:
    """La date en vigueur : le réglage s'il est posé, le défaut du code sinon."""
    import os

    brut = (os.environ.get(ENV_NULL_REFUSE_LE) or "").strip()
    if not brut:
        return NULL_REFUSE_LE
    try:
        return _date.fromisoformat(brut)
    except ValueError:
        raise ValueError(
            f"{ENV_NULL_REFUSE_LE}={brut!r} n'est pas une date `YYYY-MM-DD`. Ce "
            f"réglage décide à la fois de ce qui est ANNONCÉ et de ce qui est "
            f"REFUSÉ.") from None


def refus_arme(aujourdhui: Optional[_date] = None) -> bool:
    """Le refus est-il tombé ? — en UTC : la bascule tombe au même instant partout."""
    jour = aujourdhui or _datetime.now(_timezone.utc).date()
    return jour >= date_refus()


def nulls_nommes(user_data: Optional[dict]) -> list[str]:
    """Les colonnes que CET appel efface par un `null` explicite.

    ⚠️ **Nommé, pas déduit d'un effacement.** Une valeur peut tomber pour d'autres
    raisons (une liste remplacée, une chaîne vide) ; ce préavis ne parle que du jeton
    `null` écrit à la main, parce que c'est lui qu'on retire. Confondre les deux ferait
    crier l'avertissement sur des gestes qui ne changeront pas.
    """
    touches: list[str] = []
    for cle, valeur in (user_data or {}).items():
        if valeur is None:
            touches.append(str(cle))
        elif isinstance(valeur, dict) and dsl.VALUE_LAYER in valeur \
                and valeur[dsl.VALUE_LAYER] is None:
            touches.append(str(cle))
    return sorted(touches)


def _les_deux_gestes() -> str:
    """Les deux issues, côte à côte — le CORPS que l'avertissement et le refus
    PARTAGENT. Partagé et non recopié : celui qui s'est préparé pendant le préavis ne
    doit pas découvrir au moment du refus qu'on lui demandait autre chose."""
    return (f"Pour effacer sans rien affirmer : `{dsl.EFFACEMENT}`. Pour un vide ASSUMÉ "
            f"— aucune source ne donne cette valeur — : `{dsl.VIDE_DELIBERE}`. Pour ne pas "
            f"toucher : omets le champ, ou `{dsl.GARDE}`. ⚠️ Si `null` voulait dire "
            f"« cherché, rien trouvé » — c'est l'usage le plus courant — alors, sur une "
            f"valeur en place, le geste juste est l'OMISSION : ne rien trouver n'est pas "
            f"effacer ; sur une case vide, c'est `{dsl.VIDE_DELIBERE}`.")


def avertissement(colonnes: list[str]) -> Optional[str]:
    """Servi tant que le refus n'est pas tombé. Dit la date ET les deux gestes."""
    if not colonnes:
        return None
    from .champs_reserves import _en_francais

    return (f"`null` efface encore ici ({', '.join(f'`{c}`' for c in colonnes[:5])}"
            + (" …" if len(colonnes) > 5 else "")
            + f"), mais il sera REFUSÉ à partir du {_en_francais(date_refus())} : dans "
              f"le datastore il détruit, alors que partout ailleurs il veut dire "
              f"« pas de valeur ». Le même jeton disait une chose et son contraire "
              f"selon l'endroit. " + _les_deux_gestes())


def refus(colonnes: list[str]) -> str:
    """Le refus, une fois la date passée. MÊME corps que l'avertissement."""
    from .champs_reserves import _en_francais

    return (f"`null` n'efface plus depuis le {_en_francais(date_refus())} — rien n'a "
            f"été écrit sur {', '.join(f'`{c}`' for c in colonnes[:5])}"
            + (" …" if len(colonnes) > 5 else "")
            + f". " + _les_deux_gestes())
