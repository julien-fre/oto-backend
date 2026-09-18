"""Le modèle d'un agent, vu des capacités : le valider, et refuser de le PROMETTRE.

Partagé par `runner.triggers` et `runner.fleets` — les deux déclarent un agent, et
les deux doivent tenir la même règle avec les mêmes mots. Le catalogue lui-même
vit dans `runner_models` (pur, lu aussi par la base).
"""
from __future__ import annotations

from typing import Optional

from .. import db, runner_models
from . import _cle_exigee
from ._types import AuthzDenied


def exige_un_runner(org_id: int) -> dict:
    """Refuse de PROMETTRE une exécution que personne n'assure — et rend l'état lu,
    pour que la garde du modèle (`exige_servi`) juge sur la même lecture.

    ⚠️ Partagée par `runner.triggers` (poser un déclencheur) et `runner.fleets`
    (`launch`) — les deux gestes MENTENT de la même façon sans elle : ils
    rendent une promesse (`next_due`, `armed`) qu'aucun worker ne tient, et
    l'objet a l'air programmé sans jamais tourner. `runner_fleets#13` (oto-runner)
    a mesuré la conséquence sur `launch` : 41 travaux restés en file 13 jours,
    un armement qui a réussi et n'a jamais été repris.
    """
    etat = db.runner_arme(org_id)
    if etat["armed"]:
        return etat
    if etat["last_seen"] is None:
        detail = ("aucun worker n'a jamais sondé la file de cette org : rien "
                  "n'exécuterait ce geste")
    else:
        detail = (f"le dernier worker de cette org s'est tu le "
                  f"{etat['last_seen']} — au-delà de "
                  f"{db.ARME_FENETRE_S // 60} minutes on ne le tient plus pour "
                  f"présent")
    raise AuthzDenied(
        400, "no_runner_armed",
        f"aucun runner armé pour cette org ({detail}). L'exécution appartient "
        "au worker, et sans worker le geste réussirait pour rien, sans erreur "
        "— l'objet aurait l'air de marcher. Arme un worker pour cette org "
        "(`OTO_RUNNER_ARMED=1` + un jeton de l'org, cf. otomata-tech/oto-runner), "
        "puis reprends ce geste. Ce qui existe reste gérable sans worker : un "
        "déclencheur se lit, se modifie et se supprime ; une campagne se lit, se "
        "modifie et s'arrête (`stop`).")


def famille_declaree(model: Optional[str],
                     provider: Optional[str] = None) -> Optional[str]:
    """La famille du modèle DÉCLARÉ, ou None si aucun ne l'est. Refuse l'inconnu.

    `""` vaut absence : c'est la façon de revenir au modèle du worker.

    `provider` n'existe que sur les flottes, où il précède ce catalogue. Il ne
    choisit plus rien — la famille se DÉDUIT du modèle —, donc il ne peut que
    confirmer : un fournisseur qui contredit le modèle, ou qui arrive seul, est
    refusé plutôt qu'avalé (un champ posé qui ne s'applique pas est un défaut).
    """
    if not model:
        if provider:
            raise AuthzDenied(
                400, "invalid_model",
                f"`provider={provider}` sans `model` ne choisit rien : la famille se "
                "déduit du modèle. Nomme un modèle, ou n'envoie ni l'un ni l'autre "
                "(le worker tourne alors sur le sien).")
        return None
    f = runner_models.famille(model)
    if f is None:
        connus = ", ".join(m.id for m in runner_models.MODELES)
        raise AuthzDenied(
            400, "invalid_model",
            f"modèle inconnu : `{model}`. Modèles servis par la plateforme : {connus}.")
    if provider and provider != f:
        raise AuthzDenied(
            400, "invalid_model",
            f"`{model}` est un modèle `{f}`, pas `{provider}` — omets `provider`, il "
            "se déduit du modèle.")
    return f


def exige_servi(etat: dict, famille: Optional[str]) -> None:
    """Refuse de promettre un modèle qu'aucun worker vivant ne sert.

    ⚠️ Même asymétrie que `no_runner_armed` : un travail d'une famille que personne
    ne sert reste `pending` — le claim le filtre — puis PÉRIME à l'occurrence
    suivante. L'agent a l'air programmé et ne tourne jamais.

    ⚠️ **Ne regarde que si une famille est DEMANDÉE.** Un agent sans modèle est
    servi par n'importe quel worker ; la présence d'un runner lui suffit, et la
    garde qui la vérifie est ailleurs.

    ⚠️ **Les familles ne se lisent que chez les workers de PLATEFORME** : un worker
    au jeton d'org (l'ancien chemin) ne dépose pas sa famille, et une org servie
    par lui seul se verra refuser tout modèle explicite. Aucune org de production
    n'est servie ainsi ; le jour où une le sera, c'est ici que ça mord — et la
    réponse est de lui faire nommer son dépôt, pas de retirer la garde.
    """
    if not famille:
        return
    servies = etat.get("families") or []
    if famille in servies:
        return
    vivantes = (f"ceux qui sondent la file servent : {', '.join(servies)}"
                if servies else "aucun worker vivant n'a déclaré de famille")
    raise AuthzDenied(
        400, "model_not_served",
        f"aucun worker ne sert les modèles `{famille}` en ce moment ({vivantes}). "
        "Le travail resterait en attente sans une erreur, puis périmerait. Choisis "
        "un modèle servi (`runner.models` sur `op=list`), ou n'en nomme aucun : "
        "le worker tourne alors sur le sien.")


def etat_servi(etat: dict, org_id: Optional[int] = None) -> dict:
    """L'état du runner tel que servi : présence, familles, et le catalogue marqué
    de ce qui est servi — ce dont un écran a besoin pour proposer un modèle sans
    proposer celui qui serait refusé.

    ⚠️ Avec `org_id`, le défaut proposé écarte les familles servies dont l'org n'a
    pas déposé la clé EXIGÉE : les proposer, c'est proposer un refus
    `model_key_required` à la pose. Lu seulement quand des familles sont servies — la
    lecture du réglage est froide, et un état sans famille n'a rien à écarter."""
    familles = list(etat.get("families") or [])
    sans_cle = (_cle_exigee.manquantes(org_id, familles)
                if org_id is not None and familles else [])
    return {**etat, "families": familles,
            "models": runner_models.catalogue(familles, sans_cle)}
