"""Les gestes de l'ORDONNANCEUR d'une campagne — `take`, `beat`, `ack_stop` — et
la règle qui les tient depuis le 21/09/2026 : seul celui qui la TIENT les fait.

Sortis de `runner_fleets.py` (au-dessus de 500 lignes) quand ils ont appris à nommer
leur auteur. Ils sont servis parce que sans eux `op=stop` reste une écriture que
personne ne lit ; et ils POSENT les faits que les verbes d'opérateur n'ont pas le
droit de poser (`running`, `stopped`).

**Le preneur est un identifiant que l'ordonnanceur DÉCLARE** (`taken_by`), pas une
identité de compte : un ordonnanceur parle sous un jeton de COMPTE (`.env.fleet` chez
oto-runner), le même pour tous ceux d'une machine — `ctx.sub` ne les distingue pas, et
créer une identité par ordonnanceur ferait d'un processus un utilisateur. Il doit être STABLE à travers le redémarrage d'un même
ordonnanceur (sinon il ne reprend jamais sa campagne) et DISTINCT d'un ordonnanceur à
l'autre (sinon deux conduisent la même). oto-runner le compose de la machine et de
l'unité systemd qui le fait tourner — cf. `docs/runner-et-automatisations.md`,
« Qui tient une campagne ».

⚠️ Le refus `held_by_other` ne nomme PAS le preneur en place : il dit quoi faire, pas
qui. Qui veut savoir qui tient une campagne la lit (`op=get`), sous les droits de la
lecture.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from ._types import AuthzDenied

GESTES = ("take", "beat", "ack_stop")


def _preneur(taken_by: Optional[str], op: str) -> str:
    """Le preneur déclaré, requis : un geste d'ordonnanceur sans auteur est ce que ce
    lot existe pour empêcher."""
    preneur = (taken_by or "").strip()
    if not preneur:
        raise AuthzDenied(
            400, "missing_fields",
            f"`{op}` exige `taken_by` : l'identifiant de l'ordonnanceur, stable à "
            "travers son redémarrage et distinct de tout autre ordonnanceur. C'est "
            "lui qui dit, au redémarrage, si la campagne est la sienne.")
    return preneur


def _flotte(fleet_id: int, org_id: int) -> dict:
    actuelle = db.get_fleet(fleet_id, org_id)
    if not actuelle:
        raise AuthzDenied(404, "fleet_not_found", "flotte inconnue")
    return actuelle


def geste(org_id: int, op: str, fleet_id: int, taken_by: Optional[str],
          reason: Optional[str]) -> dict:
    preneur = _preneur(taken_by, op)

    if op == "take":
        f = db.prendre(fleet_id, org_id, preneur)
        if f:
            return {"fleet": f}
        actuelle = _flotte(fleet_id, org_id)
        if actuelle["status"] == "running":
            # ⚠️ Refus et non 200 : l'UPDATE aurait pris une campagne `running` tenue
            # par CE preneur ou par personne — il en reste une qu'un AUTRE tient.
            # Partir quand même doublerait ses exécutions.
            raise AuthzDenied(
                409, "held_by_other",
                "cette campagne tourne et un AUTRE ordonnanceur la tient : ne pars "
                "pas, deux ordonnanceurs doubleraient ses exécutions. Si celui qui la "
                "tient est mort, arrête-la (`op=stop`) puis réarme-la (`op=launch`) : "
                "le réarmement la libère.")
        raise AuthzDenied(
            409, "not_takeable",
            f"ce passage est `{actuelle['status']}` — on ne prend qu'une campagne "
            "`armed`, ou `running` pour la reprendre.")

    if op == "beat":
        # Le battement, ET la lecture de l'ordre dans le même appel : un
        # ordonnanceur qui bat sans jamais demander « dois-je m'arrêter ? »
        # laisserait `stopping` sans lecteur.
        vivant = db.battre(fleet_id, org_id, preneur)
        f = _flotte(fleet_id, org_id)
        if not vivant and f.get("taken_by") != preneur:
            # Il ne la tient pas — ou plus : réarmée depuis, elle est libre. Dans les
            # deux cas il doit l'APPRENDRE, pas continuer en croyant la conduire.
            raise AuthzDenied(
                409, "not_the_holder",
                "tu ne tiens pas cette campagne (un autre ordonnanceur la tient, ou "
                "elle a été réarmée depuis que tu l'as prise) : arrête de la conduire.")
        return {"fleet": f, "stop_requested": f["status"] in ("stopping", "stopped"),
                "beat_taken": vivant}

    # ack_stop — ⚠️ le SEUL geste qui pose `stopped`, et c'est celui qui la tient qui
    # le pose. Si un opérateur pouvait l'écrire, l'écart entre « demandé » et
    # « effectif » disparaîtrait — et avec lui le seul diagnostic d'un ordonnanceur
    # mort ; si un AUTRE ordonnanceur le pouvait, il accuserait un arrêt que le
    # preneur n'a pas exécuté.
    if not db.accuser_arret(fleet_id, org_id, reason, preneur):
        actuelle = _flotte(fleet_id, org_id)
        if actuelle["status"] in ("stopping", "running"):
            raise AuthzDenied(
                409, "not_the_holder",
                "tu ne tiens pas cette campagne : l'arrêt s'accuse par l'ordonnanceur "
                "qui la conduit, ou se constate seul quand plus aucune exécution ne "
                "tourne.")
        raise AuthzDenied(
            409, "nothing_to_acknowledge",
            f"ce passage est `{actuelle['status']}` — il n'y a pas d'arrêt en "
            "cours à accuser.")
    return {"fleet": db.get_fleet(fleet_id, org_id)}
