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

import logging
from datetime import datetime, timezone
from typing import Optional

from .. import runner_models
from ..db import runner_jobs as db_runner_jobs
from ..db import user_subscriptions
from ._types import AuthzDenied

logger = logging.getLogger(__name__)

#: Au-delà de cette part d'une fenêtre du forfait, la personne est mise en attente
#: AVANT qu'un travail ne soit refusé. Le fournisseur annonce l'usage à chaque
#: exécution (`rate_limit_event`, mesuré le 21/09/2026) : attendre le refus, c'est
#: brûler une tentative du travail pour apprendre ce qu'on savait déjà.
SEUIL_D_ATTENTE = 0.95

#: Les familles servies par un abonnement personnel. Dérivée du catalogue, jamais
#: recopiée : le jour où Codex suit le même chemin, il suffit de l'y déclarer.
FAMILLES = runner_models.FAMILLES_PERSONNELLES


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


def reparable(statut: Optional[str], bac: Optional[str]) -> bool:
    """Cet état se répare-t-il SANS toucher au travail ? Oui dès que la personne a
    un bac à sable et n'a qu'à s'y reconnecter. Non quand il n'y a rien à attendre :
    ni bac à sable, ni ligne — personne ne reviendra « reconnecter » ce qui n'a
    jamais existé, et un travail en attente éternelle est un silence."""
    return bool(bac) and statut in (user_subscriptions.A_RECONNECTER,
                                    user_subscriptions.DECONNECTE)


def raison_de_l_attente(famille: str, statut: Optional[str]) -> str:
    return (f"en attente : ce travail tourne sur l'abonnement `{famille}` de son "
            f"demandeur, qui doit s'y reconnecter ({statut}). Il repartira tout seul "
            "à la reconnexion (Réglages › Fournisseurs de modèles).")


def peut_agir_pour(sub: str, proprietaire: Optional[str], famille: str) -> bool:
    """`sub` a-t-il le droit de faire tourner QUELQUE CHOSE sur l'abonnement de
    `proprietaire` ? **LA couture du partage** (arbitré le 21/09/2026).

    Aujourd'hui : le propriétaire seul. Demain, une connexion d'abonnement
    s'administrera comme les autres connecteurs — partagée avec des personnes
    nommées, qui pourront alors modifier ses agents. Ce jour-là, la règle change
    ICI et nulle part ailleurs : les trois chemins de pose et la retouche d'un
    agent relisent tous cette fonction."""
    return not proprietaire or proprietaire == sub


def exiger_le_droit_de_modifier(sub: str, agent: dict, famille: Optional[str],
                                champs: dict) -> None:
    """Retoucher l'agent d'un AUTRE posé sur un abonnement : refusé, sauf l'éteindre.

    Changer sa procédure, sa consigne ou ses outils, c'est faire exécuter SES
    instructions sur le forfait d'un autre — la même faute que changer son modèle,
    par une autre porte. ÉTEINDRE reste ouvert à qui administre l'org : personne
    ne doit avoir besoin du propriétaire pour arrêter un agent qui dérape (la
    suppression, elle, ne passe pas par ici)."""
    if not est_abonnement(famille):
        return
    if peut_agir_pour(sub, agent.get("sub"), famille):
        return
    if set(champs) <= {"enabled"} and champs.get("enabled") is False:
        return
    raise AuthzDenied(
        400, "subscription_personal_only",
        f"cet agent tourne sur l'abonnement `{famille}` de quelqu'un d'autre : ce que "
        "tu y changerais s'exécuterait sur SON forfait. Tu peux l'éteindre ; pour le "
        "modifier, il faut que sa connexion soit partagée avec toi.")


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
    if not peut_agir_pour(sub, proprietaire, famille):
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


def noter_rapport(conclu: dict, ok: bool, resultat: Optional[dict]) -> None:
    """Ce que le worker a VU du forfait en exécutant ce travail, porté sur la
    connexion de son demandeur. Appelé à la conclusion, pour un worker de
    plateforme seulement (l'appelant le garantit).

    Le rapport voyage dans le résultat déclaré, clé `abonnement` :

        {"etat": "allowed" | <autre>,            # `rate_limit_info.status`
         "deconnecte": true,                     # le programme n'a plus de session
         "fenetres": {"five_hour": {"utilization": 0.07, "resetsAt": 1790029800},
                      "seven_day": {"utilization": 0.49, "resetsAt": 1790053200}}}

    ⚠️ DEUX fenêtres, pas une : un forfait s'épuise sur cinq heures OU sur sept
    jours, et l'échéance à attendre est celle de la fenêtre saturée — la plus
    LOINTAINE s'il y en a deux, sinon la personne repartirait pour retomber.

    ⚠️ Jamais une levée : ce rapport est un à-côté de la conclusion. Un rapport mal
    formé se journalise et s'ignore — faire échouer `complete` pour lui laisserait
    un travail TERMINÉ re-servi à l'expiration de son bail.
    """
    famille, porteur = conclu.get("model_family"), conclu.get("sub")
    if not est_abonnement(famille) or not porteur:
        return
    try:
        rapport = (resultat or {}).get("abonnement")
        if not isinstance(rapport, dict):
            # Aucun rapport : un succès prouve au moins que la session tient.
            if ok:
                user_subscriptions.marquer_statut(
                    porteur, famille, user_subscriptions.CONNECTE, ok=True,
                    observe=True)
            return
        if rapport.get("deconnecte") is True:
            user_subscriptions.marquer_statut(
                porteur, famille, user_subscriptions.A_RECONNECTER, observe=True)
            return
        echeances = [
            f["resetsAt"] for f in (rapport.get("fenetres") or {}).values()
            if isinstance(f, dict)
            and isinstance(f.get("resetsAt"), (int, float))
            and isinstance(f.get("utilization"), (int, float))
            and f["utilization"] >= SEUIL_D_ATTENTE]
        refuse = rapport.get("etat") not in (None, "allowed")
        if refuse and not echeances:
            # Refusé sans fenêtre saturée lisible : toutes les échéances connues
            # comptent, faute de savoir laquelle a mordu.
            echeances = [f["resetsAt"] for f in (rapport.get("fenetres") or {}).values()
                         if isinstance(f, dict)
                         and isinstance(f.get("resetsAt"), (int, float))]
        if echeances or refuse:
            quand = (datetime.fromtimestamp(max(echeances), tz=timezone.utc)
                     if echeances else None)
            user_subscriptions.marquer_statut(
                porteur, famille, user_subscriptions.PLAFOND, limit_reset_at=quand,
                observe=True)
            return
        user_subscriptions.marquer_statut(
            porteur, famille, user_subscriptions.CONNECTE, ok=bool(ok),
            observe=True)
    except Exception:
        logger.warning("rapport d'abonnement illisible pour le travail conclu "
                       "(famille %s) — ignoré, la conclusion tient", famille,
                       exc_info=True)


def noter_rapport_du_travail(job_id: int, ok: bool, resultat: Optional[dict]) -> None:
    """`noter_rapport`, en lisant soi-même à qui le travail appartenait.

    ⚠️ La LECTURE aussi est sous la garde (revue du 21/09/2026). Faite chez
    l'appelant, un hoquet de base à cet instant sortait en 500 d'un `complete`
    dont le travail était DÉJÀ conclu — et la libération des lignes du run, qui
    suit, ne s'exécutait jamais. Un à-côté ne doit pas pouvoir casser le geste
    qu'il accompagne, ni par son écriture, ni par sa lecture."""
    try:
        conclu = db_runner_jobs.porteur_et_famille(job_id) or {}
    except Exception:
        logger.warning("porteur du travail %s illisible — rapport d'abonnement "
                       "ignoré, la conclusion tient", job_id, exc_info=True)
        return
    noter_rapport(conclu, ok, resultat)
