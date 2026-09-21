"""La garde des travaux qui tournent sur l'ABONNEMENT d'une personne (OTO-130).

Un travail de famille `claude_subscription` ne consomme NI la clé de son org NI
celle de la plateforme : il s'exécute dans le bac à sable de son demandeur, sur le
programme officiel du fournisseur, où cette personne s'est connectée elle-même.

Trois refus nommés, tous à l'écriture ou à la réservation, jamais silencieux :

1. `subscription_personal_only` — un abonnement ne sert que les agents de son
   propriétaire. Une flotte, ou l'agent d'un collègue, ferait payer le forfait
   d'une personne pour le travail d'une autre.
2. `subscription_not_connected` — la personne n'a pas (ou plus) de session
   ouverte dans son bac à sable.
3. Au claim, un travail dont le porteur n'est plus connecté est ARRÊTÉ avec sa
   raison, comme un travail sans clé déposée : le remettre en file le ferait
   reprendre indéfiniment par le worker suivant.

⚠️ **Ce module ne lit jamais de session.** Il lit un ÉTAT (`user_model_
subscriptions.statut`), écrit par la sonde du bac à sable. La session elle-même
ne traverse pas le backend — c'est la condition qui rend ce chemin licite.
"""
from __future__ import annotations

from typing import Optional

from .. import runner_models
from ..db import user_subscriptions
from ._types import AuthzDenied

#: Les familles servies par un abonnement personnel. Dérivée du catalogue, jamais
#: recopiée : le jour où Codex suit le même chemin, il suffit de l'y déclarer.
FAMILLES = frozenset(m.family for m in runner_models.MODELES
                     if m.family.endswith("_subscription"))


def est_abonnement(famille: Optional[str]) -> bool:
    return bool(famille) and famille in FAMILLES


def famille_du_travail(job: dict) -> Optional[str]:
    """La famille portée par la charge d'un travail — la même lecture que la
    réservation (`claim_next_job` route sur `payload->>'model_family'`)."""
    return (job.get("payload") or {}).get("model_family")


_PAS_CONNECTE = (
    "ce travail demande un modèle `{famille}`, qui tourne sur l'abonnement de la "
    "personne qui l'a demandé — et cette personne n'a pas de connexion ouverte "
    "({statut}). Travail non exécuté. Ouvre la connexion (Réglages › Fournisseurs de "
    "modèles), puis rallume l'agent.")


def raison_du_refus(famille: str, statut: Optional[str]) -> str:
    return _PAS_CONNECTE.format(famille=famille, statut=statut or "jamais connectée")


def servable(sub: Optional[str], famille: str) -> tuple[bool, Optional[str], Optional[str]]:
    """La connexion de `sub` peut-elle servir un travail `famille` ?

    Rend `(servable, statut, sandbox_id)`. Un porteur absent n'est jamais servable :
    sans personne, il n'y a pas d'abonnement à consommer.

    ⚠️ `paused_limit` EST servable ici, et ce n'est pas une largesse : l'attente de
    l'échéance vit dans la réservation (`claim_next_job`), pas dans cette garde.
    Deux juges du même plafond finiraient par se contredire — voir plus bas."""
    if not sub:
        return False, None, None
    ligne = user_subscriptions.get_subscription(sub, famille) or {}
    statut, bac = ligne.get("statut"), ligne.get("sandbox_id")
    if not bac:
        return False, statut, None
    if statut == user_subscriptions.CONNECTE:
        return True, statut, bac
    if statut == user_subscriptions.PLAFOND:
        # ⚠️ Un plafond n'est JAMAIS un refus ici (corrigé le 21/09/2026). Tant que
        # son échéance est future, la réservation saute la personne — ce travail
        # n'arrive donc ici qu'une fois l'échéance PASSÉE, ou inconnue. Le refuser
        # l'ARRÊTERAIT DÉFINITIVEMENT pour un plafond qui n'existe plus : la file
        # rendait le travail et la garde le tuait (mesuré en base). On sert ; si
        # le forfait est encore épuisé, le fournisseur le dira, et le worker
        # rapportera une nouvelle échéance.
        return True, statut, bac
    return False, statut, bac


def exiger_a_la_pose(sub: str, proprietaire: Optional[str], famille: Optional[str],
                     *, flotte: bool = False) -> None:
    """Le refus LISIBLE au moment de poser un agent sur un abonnement.

    `proprietaire` = le `sub` de l'agent existant (None à la création : c'est
    l'appelant qui le devient)."""
    if not est_abonnement(famille):
        return
    if flotte:
        raise AuthzDenied(
            400, "subscription_personal_only",
            f"les modèles `{famille}` tournent sur l'abonnement d'UNE personne : une "
            "flotte appartient à l'organisation et ferait payer son forfait pour le "
            "travail de tous. Choisis un modèle servi par une clé d'organisation.")
    if proprietaire and proprietaire != sub:
        raise AuthzDenied(
            400, "subscription_personal_only",
            f"cet agent appartient à quelqu'un d'autre, et les modèles `{famille}` "
            "tournent sur l'abonnement de leur propriétaire. Seule la personne qui "
            "possède l'agent peut le poser sur le sien.")
    servable_, statut, _ = servable(sub, famille)
    if not servable_:
        etat = statut or "jamais connectée"
        raise AuthzDenied(
            400, "subscription_not_connected",
            f"aucune connexion `{famille}` ouverte pour toi ({etat}). Ouvre-la dans "
            "Réglages › Fournisseurs de modèles, puis pose l'agent : un agent posé "
            "sans connexion resterait programmé sans jamais tourner.")
