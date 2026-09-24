"""Le commerce POSE les droits déclarés de l'org (ADR 0070 §7) : la réconciliation.

Le cœur relit `org_entitlements` à chaque usage et ne sait pas qui paie. Ce module est
le producteur « commerce » : il dérive, de l'état du commerce, les lignes que ce
dernier doit porter, et les aligne.

**Rejouable, pas événementielle.** `reconcilier(org)` relit l'état entier de l'org et
remet ses lignes d'aplomb. Chaque geste qui change l'état l'appelle ensuite
(souscription, échéance, résiliation, reprise, plan offert, don d'option d'org), mais
un geste oublié ou un appel qui a échoué se rattrape au passage suivant : au boot, par
la commande `oto-mcp maintenance droits` (timer quotidien), au tick du runner
d'échéances. Même parade que le runner sur ses échéances.

**Elle ne retire que ce que ses propres sources ont posé** (`SOURCES`). Une ligne
posée sous une autre étiquette (un essai, un droit qu'un partenaire écrirait lui-même
sous la sienne…) ne lui appartient pas : elle ne la réécrit pas et ne l'efface pas.

Ce qu'elle dérive, et sous quelle source :

| état du commerce | source | échéance |
| --- | --- | --- |
| abonnement payé `active` | `subscription` | fin de période + délai de grâce |
| abonnement payé résilié (toujours `active`) | `subscription` | fin de période |
| abonnement payé `past_due` | `subscription` | fin de la grâce |
| abonnement offert (`comp`) | `offered` | sa fin de période, ou aucune |
| don d'option posé sur l'org | `offered` | l'échéance du don |
| les deux derniers, org hébergée par un partenaire | `partner` | aucune |
| abonnement réglé hors plateforme (`contract`) | `contract` | sa date de fin, ou aucune (reconduction tacite) ; + `members_max` = licences |

Le grain PERSONNE (don d'option à un compte) n'écrit rien ici : seule l'org porte un
droit payant.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import billing
from . import entitlements_catalogue as catalogue
from .db import billing as db_billing
from .db import entitlements as db_entitlements

logger = logging.getLogger(__name__)

SOURCE_SUBSCRIPTION = "subscription"
SOURCE_OFFERED = "offered"
SOURCE_PARTNER = "partner"
SOURCE_CONTRACT = "contract"
# Les étiquettes que CE producteur pose, donc les seules qu'il retire.
SOURCES = (SOURCE_SUBSCRIPTION, SOURCE_OFFERED, SOURCE_PARTNER, SOURCE_CONTRACT)
# L'auteur d'une ligne que le commerce pose de lui-même (abonnement, plan offert) ; un
# don garde l'auteur que l'admin y a inscrit.
AUTEUR = "billing"

# Une ligne voulue : (droit, source) → (échéance, auteur, valeur, début). Échéance `None`
# = sans échéance ; valeur jamais vide (oui/non = 1, #1066) ; début `None` = maintenant.
_Voulus = dict[tuple[str, str], tuple]


def delai_de_grace() -> timedelta:
    """Ce qu'un abonnement payé garde de droit après sa fin de période tant qu'il est
    `active` : les relances d'échéance (J+3, J+6), puis la grâce contractuelle qui suit
    le passage en impayé. L'état `past_due` porte ensuite sa propre date (`grace_until`),
    qui prend le relais. Dérivé des réglages du runner, jamais recopié."""
    from . import billing_runner
    return (billing_runner._RETRY_DELAY * (billing_runner._MAX_ATTEMPTS - 1)
            + billing_runner._GRACE)


def _instant(epoch) -> Optional[datetime]:
    return None if epoch is None else datetime.fromtimestamp(float(epoch), timezone.utc)


def _plus_tardive(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    """Deux raisons d'ouvrir le même droit sous la même source : la plus généreuse
    gagne, et `None` (sans échéance) l'emporte sur toute date."""
    return None if a is None or b is None else max(a, b)


def _poser(voulus: _Voulus, droit: str, source: str, fin: Optional[datetime],
           auteur: Optional[str] = AUTEUR, *, valeur: int = 1,
           debut: Optional[datetime] = None) -> None:
    cle = (droit, source)
    if cle in voulus:
        voulus[cle] = (_plus_tardive(voulus[cle][0], fin),) + voulus[cle][1:]
    else:
        voulus[cle] = (fin, auteur, valeur, debut)


def _fin_de_l_abonnement_paye(etat: dict) -> Optional[datetime]:
    """L'échéance des droits d'un abonnement payé, ou `None` s'il n'en ouvre aucun."""
    fin_de_periode = _instant(etat["period_end"])
    if etat["status"] == "active":
        if fin_de_periode is None:
            # Un abonnement payé sans fin de période n'a jamais été activé par
            # `confirm` : on ne lui ouvre rien plutôt que d'ouvrir sans borne.
            logger.error("droits: abonnement payé sans fin de période (plan %s) — "
                         "aucun droit posé", etat["plan"])
            return None
        return (fin_de_periode if etat["canceled"]
                else fin_de_periode + delai_de_grace())
    if etat["status"] == "past_due":
        return _instant(etat["grace_until"]) or (
            fin_de_periode + delai_de_grace() if fin_de_periode else None)
    return None


def _droits_du_contrat(etat: dict, voulus: _Voulus) -> None:
    """Un abonnement réglé hors plateforme : ce que son plan ouvre, plus le nombre de
    licences, jusqu'à sa date de fin (aucune = reconduction tacite), à partir de sa
    date de début. Traité comme un abonnement : payé, simplement pas ici."""
    if etat["status"] != "active":
        return
    fin, debut = _instant(etat["period_end"]), _instant(etat["contract_start"])
    from .access.entitlements import MEMBERS_MAX
    for droit in billing.plan_rights(etat["plan"]):
        _poser(voulus, droit, SOURCE_CONTRACT, fin, debut=debut)
    if etat["contract_seats"] is not None:
        _poser(voulus, MEMBERS_MAX, SOURCE_CONTRACT, fin,
               valeur=int(etat["contract_seats"]), debut=debut)


def _droits_de_l_abonnement(etat: dict, partenaire: bool, voulus: _Voulus) -> None:
    if etat["provider"] == "contract":
        _droits_du_contrat(etat, voulus)
        return
    if etat["provider"] == "comp":
        if etat["status"] != "active":
            return
        source = SOURCE_PARTNER if partenaire else SOURCE_OFFERED
        fin = None if partenaire else _instant(etat["period_end"])
    else:
        source = SOURCE_SUBSCRIPTION
        fin = _fin_de_l_abonnement_paye(etat)
        if fin is None:
            return
    for droit in billing.plan_rights(etat["plan"]):
        _poser(voulus, droit, source, fin)


def droits_voulus(org_id: int) -> _Voulus:
    """Les lignes que le commerce doit porter pour cette org, dérivées de son état."""
    partenaire = billing._hosted_by_partner(org_id)
    voulus: _Voulus = {}
    etat = db_billing.subscription_rights_state(org_id)
    if etat:
        _droits_de_l_abonnement(etat, partenaire, voulus)
    for don in db_billing.org_option_comp_bounds(org_id):
        if don["option"] not in catalogue.FIXES:
            # Un don d'option hors catalogue (`beta`, un drapeau de population) n'est pas
            # un droit déclaré : la pose le refuserait (#1066). Il reste lu là où il vit.
            continue
        if partenaire:
            _poser(voulus, don["option"], SOURCE_PARTNER, None, don["granted_by"])
        else:
            _poser(voulus, don["option"], SOURCE_OFFERED, _instant(don["expires_at"]),
                   don["granted_by"])
    return voulus


def reconcilier(org_id: int, *, dry_run: bool = False) -> dict:
    """Aligne les lignes de `SOURCES` de cette org sur l'état du commerce. **Rejouable.**

    Rend `{"poses": n, "retires": m}` — ce qui a été (ou serait, à blanc) posé et
    retiré. Poser rejoue une ligne déjà juste (upsert) : le compte dit ce que le
    commerce déclare, pas ce qui a changé."""
    voulus = droits_voulus(org_id)
    en_place = {(r["right_key"], r["source"])
                for r in db_entitlements.list_for_org(org_id) if r["source"] in SOURCES}
    a_retirer = sorted(en_place - set(voulus))
    if not dry_run:
        for (droit, source), (fin, auteur, valeur, debut) in sorted(voulus.items()):
            db_entitlements.grant(org_id, droit, source, value=valeur, starts_at=debut,
                                  expires_at=fin, granted_by=auteur)
        for droit, source in a_retirer:
            db_entitlements.revoke(org_id, droit, source)
    return {"poses": len(voulus), "retires": len(a_retirer)}


def reconcilier_tout(*, dry_run: bool = False) -> dict:
    """La reprise : réconcilie chaque org qui porte un état de commerce ou une ligne de
    nos sources. Idempotente — la rejouer ne change rien. Fail-open PAR ORG : une org
    récalcitrante est journalisée et n'arrête pas les autres, et `echecs` le dit."""
    orgs = db_billing.orgs_with_commercial_rights(SOURCES)
    poses = retires = echecs = 0
    for org_id in orgs:
        try:
            out = reconcilier(org_id, dry_run=dry_run)
        except Exception:  # noqa: BLE001 — une org n'arrête pas la reprise des autres
            echecs += 1
            logger.error("droits: réconciliation de l'org %s en échec", org_id,
                         exc_info=True)
            continue
        poses += out["poses"]
        retires += out["retires"]
    return {"orgs": len(orgs), "poses": poses, "retires": retires, "echecs": echecs}
