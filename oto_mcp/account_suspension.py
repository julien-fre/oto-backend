"""Compte mis en pause : le prédicat, et la phrase qui le refuse.

**Le geste qui manquait.** Un compte n'avait que deux états : vivant, ou supprimé —
et « supprimé » n'existe même pas comme geste de produit : le seul `DELETE FROM users`
du dépôt est l'étape 4 de `db.migrate_sub`. Or supprimer ne neutralise pas
proprement : la plupart des tables keyed-by-sub n'ont **aucune** FK vers `users`
(appartenances, projets, documents, journal…), donc la suppression laisse des
pointeurs morts plutôt que de nettoyer, et ce qui a une FK part en cascade. Entre les
deux, il n'y avait rien.

`users.suspended_at` est ce cran manquant : **le compte ne peut plus rien faire, et
rien de ce qui pend de lui n'est touché.** Ses appartenances restent, ses projets et
ses documents restent à lui, le journal continue de dire qu'il a fait ce qu'il a fait.
Un document qu'il a écrit dans une org continue de le nommer comme auteur.

**Ce que ce module porte, et pourquoi il existe séparément.** Le prédicat vit dans
`db.get_suspension` ; la DÉCISION de refuser et le TEXTE du refus vivent ici, une
seule fois, pour que les deux faces disent exactement la même chose. Une pause qui se
raconterait autrement au dashboard qu'à l'agent serait un mécanisme dont personne ne
peut vérifier l'effet.

**Le refus tombe à l'entrée de CHAQUE requête, pas au login.** C'est le seul point qui
compte : un jeton émis avant la pause reste signé et valide jusqu'à son expiration —
une heure pour un JWT, potentiellement sans limite pour un jeton `oto_`. Une pause
vérifiée à la connexion ne protégerait de rien pendant tout ce temps ; ce serait un
bouton qui rassure sans agir. Le coût est une lecture sur clé primaire par requête, à
côté d'un `upsert_user` que la face REST fait déjà à chaque appel.

**Ce qu'il a prêté s'arrête avec lui** (oto-backend#898, arbitrage du 23/09/2026,
option A). Un compte de connecteur prêté (`connector_account_grants`, nominatif ou de
groupe) et une clé prêtée à un pair (`share_side`, ADR 0044) cessent de servir leurs
bénéficiaires pendant la pause, et reprennent au réveil sans rien reconfigurer : rien
n'est détaché, c'est la résolution qui lit l'état du prêteur à chaque appel. Le
bénéficiaire, lui, n'est pas en pause — il reçoit un refus qui le DIT
(`PreteurEnPause`, code `lender_suspended`), pas « révoqué » ni « introuvable ».

Ce que ce module ne fait PAS : décider QUI peut mettre en pause (c'est l'autorisation
de la capacité `admin.account`), ni empêcher un compte en pause de disparaître d'un
merge (c'est la garde de `db.migrate_sub`).
"""
from __future__ import annotations

import logging
from typing import Optional

from . import db

logger = logging.getLogger(__name__)

# Le code servi aux DEUX faces, sans traduction : un signal remonté par un agent se
# retrouve tel quel dans le journal, et un intégrateur n'a qu'une chaîne à connaître.
CODE = "account_suspended"
# Le refus servi au BÉNÉFICIAIRE d'un prêt dont le prêteur est en pause (#898). Code
# distinct : celui qui le reçoit n'est pas en pause, et confondre les deux l'enverrait
# demander le réveil d'un compte qui n'est pas le sien.
CODE_PRETEUR = "lender_suspended"


class PreteurEnPause(ValueError):
    """Le connecteur visé est PRÊTÉ par un compte mis en pause (#898, option A).

    `ValueError` parce que c'est la famille que les chemins de résolution d'identité
    lèvent déjà pour « ce compte n'est pas opérable » (et que leurs appelants
    convertissent en refus) ; la sous-classe porte le code qui le NOMME, pour qu'une
    surface qui veut distinguer la pause d'une révocation le puisse."""

    code = CODE_PRETEUR

    def __init__(self, preteur: str, quoi: str):
        self.preteur = preteur
        super().__init__(message_preteur(preteur, quoi))


def etat(sub: str) -> Optional[dict]:
    """L'état de pause d'un compte, ou `None` s'il est vivant — le cas de tout le monde.

    ⚠️ Ne rattrape rien : un hoquet de base REMONTE. Rendre `None` sur une panne
    servirait la requête d'un compte neutralisé comme s'il était vivant, c'est-à-dire
    qu'un incident de base ferait sauter la garde, en silence et sans une ligne. Le
    fail-safe d'une neutralisation est le refus, pas le laisser-passer."""
    if not sub:
        return None
    return db.get_suspension(sub)


def message(pause: dict) -> str:
    """La phrase servie au porteur du jeton — la même des deux côtés.

    Elle dit trois choses, et c'est le minimum pour que le refus soit actionnable :
    que le compte est en pause (pas cassé, pas inconnu), le motif écrit par celui qui
    l'a posée, et que le retour passe par un humain. Elle ne nomme pas l'opérateur :
    le motif suffit à retrouver la décision, et l'identité de qui l'a prise appartient
    à l'exploitant, pas au porteur du jeton."""
    motif = (pause.get("suspended_reason") or "").strip()
    fin = f" Motif : {motif}" if motif else ""
    return ("Ce compte est en pause : il ne peut plus agir, et rien de ce qui lui "
            "appartient n'a été supprimé." + fin +
            " Le réveil est un acte d'administration — demandez-le à l'administrateur "
            "de votre espace.")


def message_preteur(preteur: str, quoi: str) -> str:
    """La phrase servie au bénéficiaire d'un prêt retenu par la pause du prêteur.

    Elle dit ce qui s'arrête et pourquoi, que ce n'est ni une révocation ni une panne,
    que le prêt reviendra tel quel, et ce qu'on peut faire d'ici là. Elle ne donne PAS
    le motif de la pause : il appartient à l'exploitant et au compte visé, pas à ceux
    à qui ce compte prêtait."""
    return (f"{quoi} t'est prêté par {preteur}, dont le compte est en pause : le "
            "prêt est suspendu avec lui — ni révoqué, ni supprimé. Il reprendra tel "
            "quel à son réveil, sans rien reconfigurer de ton côté. D'ici là, agis "
            "sous une autre identité (oto_identity(op='list'), oto_instance(op='list')) "
            "ou demande le réveil à l'administrateur de son espace.")


def refus_preteur(sub: str, quoi: str) -> Optional[PreteurEnPause]:
    """Le refus à lever si `sub`, qui PRÊTE `quoi`, est en pause — `None` sinon.

    Même source unique que la garde d'entrée (`etat`), et même règle : un hoquet de
    base remonte. Le prêteur est nommé par son email (c'est ainsi que le bénéficiaire
    le connaît), par son sub à défaut."""
    if etat(sub) is None:
        return None
    user = db.get_user(sub) or {}
    return PreteurEnPause(user.get("email") or sub, quoi)


def refus(sub: str) -> Optional[tuple[str, dict]]:
    """`(message, état)` si le compte est en pause, `None` sinon.

    Point d'appel unique des deux gardes d'entrée (`api.base._authenticate` côté REST,
    `AccountSuspendedMiddleware` côté MCP). Journalise chaque refus en `warning` : un
    compte en pause qui continue de frapper à la porte est un fait d'exploitation qu'on
    veut voir — c'est ce qui dira si la pause a été comprise, ou si une automatisation
    tourne encore sous cette identité."""
    pause = etat(sub)
    if not pause:
        return None
    logger.warning("compte en pause refusé à l'entrée : sub=%s depuis=%s",
                   sub, pause.get("suspended_at"))
    return message(pause), pause
