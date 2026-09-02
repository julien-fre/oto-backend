"""Les AVANTAGES OFFERTS — ce qu'une org reçoit gratuitement, et à qui on le dit.

Un avantage payant peut être ouvert sans qu'aucun abonnement n'existe : un admin
plateforme pose un **don d'option** (`option_comps`, couche 3 d'ADR 0043) et le droit
s'ouvre immédiatement. C'est un chemin distinct de l'abonnement offert
(`billing.admin_set_plan`, qui écrit une ligne `org_subscriptions` avec
`provider='comp'`), et jusqu'au 2026-09-02 il était **muet** : l'écran d'abonnement
lit l'abonnement, un don n'en écrit aucun, donc son bénéficiaire voyait un catalogue
de paliers qui lui vendait, prix affichés et bouton armé, exactement ce qu'il
possédait déjà. Mesuré le 2026-09-02 : 32 dons vivants, un seul abonnement payant sur
toute la plateforme. Ce module rend les dons LISIBLES là où on les cherche.

## Ce qu'il tient, et pourquoi ici

1. **Le catalogue des avantages** — dérivé, jamais recopié : un avantage est une
   option qui figure dans les `options` d'au moins un palier de `billing.PLANS`.
   Conséquence voulue : une option qui n'est vendue nulle part (`beta`, un drapeau de
   population choisie) n'est PAS un avantage et ne s'affiche jamais comme un cadeau.
   Son libellé vient du registre de connecteurs, pas d'une table de correspondance
   qu'il faudrait tenir à jour — l'avantage se NOMME, on ne suppose pas lequel c'est.
2. **Le périmètre** — à qui ce genre de dispositif s'adresse. Voir `org_is_ours`.

## Ce qu'il ne tient pas

L'entitlement. « L'option est-elle ouverte » reste `access.has_option` (le seam), et
l'échéance mord une fois pour toutes dans `db.has_option_comp`. Ce module ne décide
d'aucun droit : il DÉCRIT un don pour l'afficher. Un module qui déciderait aussi
serait la quatrième règle d'une question qui en a déjà trois de trop.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import db, providers, tenancy
from .access import quotas
# Le format de date servi par l'API est défini UNE fois, dans la couche DB (même
# raison qu'en tête de `billing.py`) : une réponse qui construit sa date à côté
# fabrique un second format pour le même champ.
from .db._conn import _normalize_value

logger = logging.getLogger(__name__)


def _catalogue() -> dict[str, dict]:
    """Option → `{label, detail, amount, currency, interval}`, DÉRIVÉ à l'appel.

    Deux dérivations, aucune saisie :

    - **ce qui est un avantage** : l'union des `options` des paliers de `billing.PLANS`.
      Un palier est ce qu'on vend, donc ce qu'on vend est ce qui vaut quelque chose ;
    - **combien il vaut** : le prix du palier le MOINS cher qui l'inclut — « ce que
      ça vaut » est ce qu'il faudrait payer pour l'avoir, pas le haut du catalogue.

    Le libellé vient du connecteur PORTEUR de l'option (celui qui détient le
    credential : `providers.credential_provider(n) == n`), pas des connecteurs qui
    l'empruntent — les six canaux de messagerie partagent une option et un compte,
    les nommer tous les six ferait six cadeaux d'un seul.
    """
    from . import billing  # import tardif : billing importe ce module.

    out: dict[str, dict] = {}
    for meta in billing.PLANS.values():
        for opt in meta.get("options", ()):
            cur = out.get(opt)
            if cur is None or meta["amount"] < cur["amount"]:
                out[opt] = {"amount": meta["amount"], "currency": meta["currency"],
                            "interval": meta["interval"], "label": opt, "detail": None}
    for name, con in providers.REGISTRY.items():
        opt = quotas.paid_option_for(name)
        if opt in out and providers.credential_provider(name) == name:
            out[opt]["label"] = con.label or opt
            out[opt]["detail"] = con.help or None
    return out


def is_benefit(option: str) -> bool:
    """L'option est-elle un avantage PAYANT (donc offrable, donc affichable comme un
    cadeau) ? `beta` et consorts : non — un drapeau de population n'a pas de prix."""
    return option in _catalogue()


# ── Le périmètre : à qui ce dispositif s'adresse ─────────────────────────────

def org_is_ours(org_id: Optional[int]) -> bool:
    """L'organisation est-elle une CLIENTE DIRECTE d'Otomata ?

    `False` pour toute org hébergée par un tenant tiers. **Ce n'est pas une
    préférence d'affichage, c'est une limite de périmètre** (cadre Alexis,
    2026-09-02) : les orgs d'un partenaire sont ses clients à lui, sur ses données à
    lui, dans son produit à lui. Leur afficher « offert par Otomata jusqu'au … », leur
    poser une échéance ou les relancer, c'est s'adresser aux clients de quelqu'un
    d'autre par-dessus sa tête. Mesuré le 2026-09-02 : 11 des 20 orgs à option offerte
    sont dans ce cas — la majorité du dispositif, si personne ne filtre.

    Le rattachement est lu par `db.org_tenant_slug` (union de trois axes structurels,
    aucun nom ni domaine deviné). **Un dispositif ne s'ouvre que sur une réponse
    franche** : une lecture qui échoue rend `False`, pas « probablement à nous ».

    ⚠️ Cette fonction dit à qui on PARLE, jamais qui a le droit. Elle ne doit gater
    aucun entitlement : retirer un droit à l'org d'un partenaire serait exactement le
    même débordement, dans l'autre sens.
    """
    if not org_id:
        return False
    try:
        return db.org_tenant_slug(int(org_id)) == tenancy.PRIMARY_SLUG
    # noqa: SILENT — fail-closed de périmètre : sans réponse franche sur le tenant,
    # on se tait plutôt que de risquer de s'adresser aux clients d'un partenaire.
    except Exception:  # noqa: BLE001
        logger.warning("billing_grants: tenant de l'org %s illisible — "
                       "dispositif refermé par défaut", org_id, exc_info=True)
        return False


# ── Les dons, mis en forme pour l'écran ──────────────────────────────────────

def _shape(row: dict, scope: str, meta: dict, now: datetime) -> dict:
    exp = row.get("expires_at")
    days = None
    if isinstance(exp, datetime):
        # La colonne est TIMESTAMPTZ, mais un stub de test peut rendre un naïf :
        # aligner plutôt que lever, la mise en forme d'un écran n'a pas à casser.
        days = (exp - (now if exp.tzinfo else now.replace(tzinfo=None))).days
    return {
        "option": row["option"],
        # NOMME l'avantage : « il n'y a pas que l'option de messagerie qui coûte »
        # (Alexis, 2026-09-02). Le jour où un second avantage s'offre, cette ligne
        # dit lequel sans qu'on ait à y revenir.
        "label": meta["label"],
        "detail": meta["detail"],
        "scope": scope,          # 'org' = offert à l'espace | 'user' = à ce compte
        "granted_at": _normalize_value(row.get("granted_at")),
        "expires_at": _normalize_value(exp),
        # Négatif = l'échéance est PASSÉE. On le rend plutôt que de le borner à zéro :
        # un don échu doit se lire comme échu, pas comme « expire aujourd'hui ».
        "days_left": days,
        "value_amount": meta["amount"],      # centimes HT — ce qu'il faudrait payer
        "currency": meta["currency"],
        "interval": meta["interval"],
    }


def granted_benefits(org_id: Optional[int], *, sub: Optional[str] = None) -> list[dict]:
    """Les avantages payants OFFERTS dont bénéficie cette org (et, si `sub` est
    donné, ce compte-là), prêts à afficher.

    `sub` est le grain de l'appelant : `/api/me/billing` le passe (l'écran est le
    sien, et 12 des 32 dons sont posés sur un COMPTE, pas sur un espace) ; la fiche
    d'org servie à un admin plateforme ne le passe pas — sinon un admin porteur d'un
    don personnel verrait toutes les orgs de la plateforme comme gratifiées. C'est le
    même anti-fuite de contexte que le `org=` explicite d'`access.has_option`.

    Rend `[]` — jamais un refus, jamais une exception — dès que l'org sort du
    périmètre : un écran qui s'affiche à moitié vaut mieux qu'un écran qui parle à la
    place d'un partenaire.
    """
    if not org_is_ours(org_id):
        return []
    cat = _catalogue()
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for scope, eid in (("org", str(org_id)), ("user", sub)):
        if not eid:
            continue
        for row in db.list_option_comp_rows(scope, eid):
            meta = cat.get(row["option"])
            if meta is None:          # option sans prix (drapeau) : pas un cadeau
                continue
            out.append(_shape(row, scope, meta, now))
    # Un même avantage peut être offert deux fois (au compte ET à l'espace) : on ne
    # l'annonce qu'une fois, en gardant l'échéance la plus LOINTAINE — c'est celle
    # qui décrit jusqu'à quand le bénéficiaire l'a réellement, l'autre étant
    # recouverte. `None` (perpétuel) l'emporte sur toute date.
    best: dict[str, dict] = {}
    for b in out:
        cur = best.get(b["option"])
        if cur is None or _later(b["expires_at"], cur["expires_at"]):
            best[b["option"]] = b
    return [best[k] for k in sorted(best)]


def _later(a: Optional[str], b: Optional[str]) -> bool:
    """`a` couvre-t-il plus loin que `b` ? `None` = perpétuel, donc le plus loin."""
    if a is None:
        return True
    if b is None:
        return False
    return a > b


# ── L'usage inclus : dire qu'oto n'est pas sans fin ──────────────────────────

# Appels d'outil d'agent inclus par mois et par organisation (cadre Alexis,
# 2026-09-02). ⚠️ **Ce n'est pas un plafond de refus** : rien, nulle part, ne doit
# refuser un appel ni facturer sur cette base. Le journal qui porte le chiffre est
# best-effort et non transactionnel — bâtir un refus dessus reviendrait à couper un
# service sur une donnée qui a le droit de manquer.
#
# La valeur est CHOISIE pour ne mordre sur personne : mesuré sur août 2026 (clients
# directs, partenaire écarté), 16 orgs actives, un maximum à 516 appels et une
# MÉDIANE à 25. À 1000, aucune org ne dépasse. C'est délibéré — le compteur est là
# pour rendre l'usage visible et poser qu'oto a une limite, pas pour la faire sentir.
INCLUDED_CALLS_PER_MONTH = 1000


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def monthly_usage(org_id: Optional[int]) -> Optional[dict]:
    """Appels d'outil d'agent de l'org depuis le 1er du mois, et ce qui est inclus.

    `None` quand il n'y a rien à montrer : org hors périmètre (un partenaire compte
    ses clients lui-même) ou journal illisible. Un compteur qui affiche « 0 » parce
    qu'il n'a pas su lire est pire que pas de compteur — c'est le seul chiffre de
    l'écran que personne ne peut recouper.

    **MOIS EN COURS uniquement, et c'est une contrainte de donnée, pas un choix de
    produit** : la purge du journal ne garde qu'environ 35 jours (la politique en
    annonce 90 ; l'écart est un artefact corrigé le 2026-08-28). Le mois en cours est
    donc toujours calculable — 31 jours au pire — et le mois PRÉCÉDENT ne l'est pas.
    Ne pas bâtir de comparaison « vs mois dernier » sur cette source avant que la
    rétention réelle ait rattrapé la politique.

    **Aucun ratio n'est servi, délibérément.** À 25 sur 1000, un pourcentage ou une
    barre de progression dit « c'est gratuit et sans fin » — l'inverse exact de ce
    que le compteur doit faire comprendre. On rend le NOMBRE d'appels et le plafond
    inclus ; la surface les met côte à côte sans les diviser.
    """
    if not org_is_ours(org_id):
        return None
    now = datetime.now(timezone.utc)
    debut = _month_start(now)
    try:
        calls = db.count_org_mcp_calls(int(org_id), since=debut)
    # noqa: SILENT — un compteur qui n'a pas su lire se TAIT : afficher « 0 » ferait
    # passer une panne de lecture pour une absence d'usage.
    except Exception:  # noqa: BLE001
        logger.warning("billing_grants: usage de l'org %s illisible", org_id,
                       exc_info=True)
        return None
    return {
        "calls": calls,
        "included": INCLUDED_CALLS_PER_MONTH,
        "period_start": _normalize_value(debut),
        # Nommé plutôt que laissé à dériver : c'est ce booléen qui doit décider d'un
        # message, jamais un pourcentage calculé côté écran. Et il n'entraîne AUCUN
        # refus — le dépassement s'affiche, il ne coupe pas.
        "over": calls > INCLUDED_CALLS_PER_MONTH,
    }
