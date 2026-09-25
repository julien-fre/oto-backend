"""La garde des travaux qui tournent sur l'ABONNEMENT d'une personne (OTO-130).

Un travail de famille `claude_subscription` ne consomme NI la clé de son org NI
celle de la plateforme : il s'exécute dans le sandbox de son demandeur, sur le
programme officiel du fournisseur, où cette personne s'est connectée elle-même.

Trois refus nommés, tous à l'écriture ou à la réservation, jamais silencieux :

1. `subscription_personal_only` — un abonnement ne sert que les agents de son
   propriétaire. Une flotte, ou l'agent d'un collègue, ferait payer le forfait
   d'une personne pour le travail d'une autre.
2. `subscription_not_connected` — la personne n'a pas (ou plus) de session
   ouverte dans son sandbox.
3. Au claim, un travail dont le porteur n'est plus connecté est ARRÊTÉ avec sa
   raison, comme un travail sans clé déposée : le remettre en file le ferait
   reprendre indéfiniment par le worker suivant.

**Le POOL d'org** (25/09/2026). Une org peut passer une famille en mode `pool`
(`org_subscription_pool`) : ses travaux tournent alors sur l'abonnement d'un membre
qui l'a PRÊTÉ à cette org (opt-in, par org), choisi à la réservation — le moins
récemment servi, libre, servable. Le demandeur n'a plus besoin d'une connexion à
lui (il porte toujours l'option), une flotte y passe, et le forfait rapporté est
celui du PRÊTEUR, sous SON seuil (min du plafond de l'org du travail et du sien).
Le mode personnel reste le défaut, à l'octet près.

⚠️ **Ce module ne lit jamais de session.** Il lit un ÉTAT (`user_model_
subscriptions.statut`), écrit par la sonde du sandbox. La session elle-même
ne traverse pas le backend — c'est la condition qui rend ce chemin licite.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import access, runner_models
from ..db import org_subscription_limits, org_subscription_pool
from ..db import runner_jobs as db_runner_jobs
from ..db import user_subscriptions
from ._types import AuthzDenied

logger = logging.getLogger(__name__)

#: Le PLAFOND de consommation par défaut, en % de l'usage TOTAL du compte du
#: fournisseur (fenêtres cinq heures et sept jours, usage perso compris) — celui d'une
#: org qui n'a rien réglé. Au-delà, la personne est mise en attente AVANT qu'un
#: travail ne soit refusé : le fournisseur annonce l'usage à chaque exécution
#: (`rate_limit_event`, mesuré le 21/09/2026), et attendre le refus, c'est brûler une
#: tentative pour apprendre ce qu'on savait déjà — et ne rien laisser à la personne
#: pour son propre usage (décision du 25/09/2026). Le seuil effectif : `seuil`.
DEFAUT_LIMITE_PCT = 80

#: L'OPTION qui ouvre ce chemin à une personne (`oto_admin_set_option`, entité
#: `user`). Ouvert nominativement, jamais par défaut : un abonnement personnel
#: sur une plateforme partagée reste un usage que le fournisseur n'a pas
#: confirmé par écrit (24/09/2026) — on l'ouvre à des personnes nommées.
OPTION = "claude_subscription"

#: La borne d'une échéance de plafond rapportée par un worker. Les fenêtres du
#: fournisseur durent cinq heures ou sept jours : au-delà, le rapport est faux
#: (une unité, un worker fautif), et le croire suspendrait la personne sans fin.
ECHEANCE_MAX = timedelta(days=8)

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


def reparable(statut: Optional[str], sandbox: Optional[str]) -> bool:
    """Cet état se répare-t-il SANS toucher au travail ? Oui dès que la personne a
    un sandbox et n'a qu'à s'y reconnecter. Non quand il n'y a rien à attendre :
    ni sandbox, ni ligne — personne ne reviendra « reconnecter » ce qui n'a
    jamais existé, et un travail en attente éternelle est un silence."""
    return bool(sandbox) and statut in (user_subscriptions.A_RECONNECTER,
                                    user_subscriptions.DECONNECTE)


def raison_de_l_attente(famille: str, statut: Optional[str], *, pool: bool = False) -> str:
    if pool:
        return (f"en attente : ce travail tourne sur le pool `{famille}` de "
                f"l'organisation, et l'abonnement prêté qui devait le servir ne peut "
                f"plus ({statut or 'retiré'}). Il repartira sur le prochain abonnement "
                "prêté disponible.")
    return (f"en attente : ce travail tourne sur l'abonnement `{famille}` de son "
            f"demandeur, qui doit s'y reconnecter ({statut}). Il repartira tout seul "
            "à la reconnexion (Réglages › Fournisseurs de modèles).")


def en_pool(org_id: Optional[int], famille: Optional[str]) -> bool:
    """L'org sert-elle cette famille par son POOL (abonnements prêtés par ses
    membres) plutôt que par l'abonnement de chaque demandeur ? Réglé par l'org
    (`org.model_subscriptions`), `personnel` par défaut."""
    return est_abonnement(famille) and org_subscription_pool.en_pool(org_id, famille)


_FLOTTE_HORS_POOL = (
    "les modèles `{famille}` tournent ici sur l'abonnement d'UNE personne : une flotte "
    "appartient à l'organisation et ferait payer son forfait pour le travail de tous. "
    "Choisis un modèle servi par une clé d'organisation, ou demande à un admin de "
    "passer l'organisation en mode pool (des membres y prêtent leur abonnement).")


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
    statut, sandbox = ligne.get("statut"), ligne.get("sandbox_id")
    if not sandbox:
        return False, statut, None
    if statut == user_subscriptions.CONNECTE:
        return True, statut, sandbox
    if statut == user_subscriptions.PLAFOND:
        # ⚠️ Un plafond n'est JAMAIS un refus ici (corrigé le 21/09/2026). Tant que
        # son échéance est future, la réservation saute la personne — ce travail
        # n'arrive donc ici qu'une fois l'échéance PASSÉE, ou inconnue. Le refuser
        # l'ARRÊTERAIT DÉFINITIVEMENT pour un plafond qui n'existe plus : la file
        # rendait le travail et la garde le tuait (mesuré en base). On sert ; si
        # le forfait est encore épuisé, le fournisseur le dira, et le worker
        # rapportera une nouvelle échéance.
        return True, statut, sandbox
    return False, statut, sandbox


def exiger_ouvert(sub: str, famille: str) -> None:
    """Ce chemin n'est ouvert qu'aux personnes qui portent l'option `OPTION`."""
    if not access.has_option(sub, OPTION):
        raise AuthzDenied(
            403, "subscription_not_enabled",
            f"les modèles `{famille}` tournent sur l'abonnement personnel de qui les "
            "pose, et ce chemin n'est ouvert qu'à des personnes nommées. Demande-le à "
            "l'équipe oto.")


def exiger_a_la_pose(sub: str, proprietaire: Optional[str], famille: Optional[str],
                     *, flotte: bool = False, org_id: Optional[int] = None) -> None:
    """Le refus LISIBLE au moment de poser un agent sur un abonnement.

    `proprietaire` = le `sub` de l'agent existant (None à la création : c'est
    l'appelant qui le devient). `org_id` = l'org où l'agent tournera : c'est son MODE
    qui dit quel abonnement paiera.

    **En mode pool**, ce n'est plus la connexion du demandeur qui se juge (il ne paie
    pas) mais le pool : au moins un membre de l'org doit lui prêter un abonnement
    servable, sinon l'agent resterait programmé sans jamais tourner. Une flotte y
    passe. L'option, elle, se porte toujours : le chemin reste ouvert nommément.

    ⚠️ La propriété de l'agent se juge dans les DEUX modes (`peut_agir_pour`) :
    l'org peut repasser en personnel, et l'agent d'un autre retouché pendant le pool
    tournerait alors sur SON forfait avec les consignes d'un autre."""
    if not est_abonnement(famille):
        return
    exiger_ouvert(sub, famille)
    pool = en_pool(org_id, famille)
    if flotte and not pool:
        raise AuthzDenied(400, "subscription_personal_only",
                          _FLOTTE_HORS_POOL.format(famille=famille))
    if not peut_agir_pour(sub, proprietaire, famille):
        raise AuthzDenied(
            400, "subscription_personal_only",
            f"cet agent appartient à quelqu'un d'autre, et les modèles `{famille}` "
            "tournent sur l'abonnement de leur propriétaire. Seule la personne qui "
            "possède l'agent peut le poser sur le sien.")
    if pool:
        if not org_subscription_pool.taille_du_pool(org_id, famille):
            raise AuthzDenied(
                400, "subscription_pool_empty",
                f"l'organisation fait tourner les modèles `{famille}` sur son pool, et "
                "aucun membre n'y prête d'abonnement connecté. Un membre doit prêter le "
                "sien (Réglages › Fournisseurs de modèles), puis pose l'agent : posé sur "
                "un pool vide, il resterait programmé sans jamais tourner.")
        return
    servable_, statut, _ = servable(sub, famille)
    if not servable_:
        etat = statut or "jamais connectée"
        raise AuthzDenied(
            400, "subscription_not_connected",
            f"aucune connexion `{famille}` ouverte pour toi ({etat}). Ouvre-la dans "
            "Réglages › Fournisseurs de modèles, puis pose l'agent : un agent posé "
            "sans connexion resterait programmé sans jamais tourner.")


def exiger_limite_valide(limite_pct: Optional[int]) -> None:
    """Un plafond (org ou perso) est un pourcentage entier de 1 à 100, ou `None`
    (org : revenir au défaut ; perso : aucun). 0 n'est pas un plafond : c'est ne plus
    rien servir, et le geste pour ça est de se déconnecter."""
    if limite_pct is not None and not 1 <= limite_pct <= 100:
        raise AuthzDenied(
            400, "invalid_limit",
            f"`limit_pct` doit être un entier de 1 à 100 (reçu {limite_pct}), ou null.")


def seuil(sub: str, org_id: Optional[int], famille: str) -> float:
    """La part d'une fenêtre du forfait (0..1) au-delà de laquelle les travaux de
    `sub` sur `famille`, lancés dans `org_id`, ATTENDENT la réinitialisation.

    **LA règle du plafond** (décidée le 25/09/2026), et elle n'est écrite qu'ici : le
    plafond de l'org (`DEFAUT_LIMITE_PCT` si elle n'a rien réglé), resserré par le
    plafond PERSO de la personne s'il est plus bas. Un plafond perso plus haut ne
    relâche rien : un abonnement sert l'org sous SES règles, la personne ne peut que
    se garder plus de marge."""
    reglee = org_subscription_limits.get_limite(org_id, famille) if org_id else None
    pct = reglee["limite_pct"] if reglee else DEFAUT_LIMITE_PCT
    perso = (user_subscriptions.get_subscription(sub, famille) or {}).get("limite_pct")
    if perso is not None:
        pct = min(pct, perso)
    return pct / 100


def noter_rapport(conclu: dict, ok: bool, resultat: Optional[dict]) -> None:
    """Ce que le worker a VU du forfait en exécutant ce travail, porté sur la
    connexion qui l'a SERVI (`conclu["abonnement"]` : le demandeur en mode personnel,
    le prêteur en mode pool — le seuil est alors le SIEN dans l'org du travail). Appelé à la conclusion, pour un worker de
    plateforme seulement (l'appelant le garantit).

    Le rapport voyage dans le résultat déclaré, clé `abonnement` :

        {"etat": "allowed" | <autre>,            # `rate_limit_info.status`
         "deconnecte": true,                     # le programme n'a plus de session
         "fenetres": {"five_hour": {"utilization": 0.07, "resetsAt": 1790029800},
                      "seven_day": {"utilization": 0.49, "resetsAt": 1790053200}}}

    ⚠️ DEUX fenêtres, pas une : un forfait s'épuise sur cinq heures OU sur sept
    jours, et l'échéance à attendre est celle de la fenêtre saturée — la plus
    LOINTAINE s'il y en a deux, sinon la personne repartirait pour retomber.
    « Saturée » = au-delà du plafond de consommation (`seuil`), le même pour les deux
    fenêtres. Le travail qui rapporte est déjà FINI : un plafond ne coupe jamais un
    run, il fait attendre les suivants.

    ⚠️ Jamais une levée : ce rapport est un à-côté de la conclusion. Un rapport mal
    formé se journalise et s'ignore — faire échouer `complete` pour lui laisserait
    un travail TERMINÉ re-servi à l'expiration de son bail.
    """
    # Le porteur du FORFAIT, pas forcément le demandeur : en mode pool, c'est le
    # membre qui a prêté son abonnement — sa connexion, son plafond perso.
    famille = conclu.get("model_family")
    porteur = conclu.get("abonnement") or conclu.get("sub")
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
        fenetres = rapport.get("fenetres") or {}
        plafond = seuil(porteur, conclu.get("org_id"), famille) if fenetres else None
        echeances = [
            f["resetsAt"] for f in fenetres.values()
            if isinstance(f, dict)
            and isinstance(f.get("resetsAt"), (int, float))
            and isinstance(f.get("utilization"), (int, float))
            and f["utilization"] >= plafond]
        refuse = rapport.get("etat") not in (None, "allowed")
        if refuse and not echeances:
            # Refusé sans fenêtre saturée lisible : toutes les échéances connues
            # comptent, faute de savoir laquelle a mordu.
            echeances = [f["resetsAt"] for f in fenetres.values()
                         if isinstance(f, dict)
                         and isinstance(f.get("resetsAt"), (int, float))]
        if echeances or refuse:
            # Bornée AVANT la conversion : une échéance en millisecondes déborde `fromtimestamp`,
            # et la levée faisait ignorer le rapport entier.
            borne = (datetime.now(timezone.utc) + ECHEANCE_MAX).timestamp()
            quand = (datetime.fromtimestamp(min(max(echeances), borne), tz=timezone.utc)
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
