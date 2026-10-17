"""Ce qu'une org peut encore ENGAGER sur une clé plateforme — réservé à la soumission.

**Le défaut qu'il ferme.** Un job asynchrone facturé au relevé (FullEnrich : la
soumission est gratuite, le relevé facture ce que le job a trouvé) laisse une org
soumettre bien plus que son solde : rien n'est compté avant le relevé, et le commerce
ne coupe qu'après avoir vu la facture. Une org gratuite a ainsi soumis des centaines de
contacts sous son plafond, et l'a dépassé de moitié en quelques relevés.

**Ce qu'il fait.** Le commerce pousse, par org et par connecteur, ce qui reste
engageable en unités du FOURNISSEUR (`service.org.spend_cap` : il connaît le solde et
les prix, le cœur n'en connaît aucun). Une soumission sur la clé plateforme réserve
son coût au pire (`units`, calculé par l'outil sur le barème du fournisseur) ; ce qui
ne tient pas dans le plafond est refusé AVANT d'être envoyé.

- **Pas de ligne, pas de plafond** : une org que le commerce ne plafonne pas (payante
  sans limite, autre tenant, clé propre) n'est jamais touchée.
- **Clé plateforme seulement** : sur sa propre clé, l'org paie son fournisseur.
- **Fail-open, bruyamment** : un compteur en panne journalise et sert l'appel — le
  commerce coupe toujours après coup, comme avant.
- **Qui est compté** : l'org dont la clé est consommée (`heritage.org_des_cles`), tous
  ses membres ensemble.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..auth.hooks import current_user_sub_from_token
from ..db import spend_cap as db_cap
from . import heritage, scope

logger = logging.getLogger(__name__)

#: Le code nommé du refus — testé par les appelants, jamais la phrase.
CODE = "spend_cap_exceeded"

#: Les connecteurs plafonnables, et l'outil qui ENGAGE sur chacun (celui qui réserve,
#: et dont le journal dit ce qui a été engagé depuis une poussée). Liste FERMÉE : un
#: plafond posé sur un connecteur qu'aucun outil ne réserve ne bornerait rien.
ENGAGING_TOOLS: dict[str, str] = {"fullenrich": "fullenrich_enrich_linkedin"}


class SpendCapExceeded(Exception):
    """La soumission ne tient pas dans ce que l'org peut encore engager."""

    def __init__(self, connector: str, units: int, remaining: int):
        super().__init__(f"{connector}: {units} demandés, {remaining} engageables")
        self.connector = connector
        self.units = units
        self.remaining = remaining


@dataclass(frozen=True)
class Reservation:
    org_id: int
    connector: str
    units: int


def _org() -> Optional[int]:
    # Sans identité (stdio local, banc), aucune org : rien à plafonner — la résolution
    # de clé qui précède a déjà refusé ce qui devait l'être.
    sub = current_user_sub_from_token()
    if not sub:
        return None
    return heritage.org_des_cles(sub, scope.current_org(sub))


def reserver(connector: str, units: int) -> Optional[Reservation]:
    """Réserve `units` pour l'org de l'appel. `None` = rien réservé (pas d'org, pas de
    plafond, ou compteur en panne). Lève `SpendCapExceeded` quand le plafond ne les
    couvre pas."""
    if units <= 0:
        return None
    org = None
    try:
        org = _org()
        if org is None:
            return None
        res = db_cap.reserver(org, connector, units)
    except Exception:
        logger.exception("spend cap: réservation impossible org=%s %s — appel servi",
                         org, connector)
        return None
    if res is None:
        return None
    if not res["ok"]:
        raise SpendCapExceeded(connector, units, max(0, res["units_left"] - res["engaged"]))
    return Reservation(org, connector, units)


def liberer(res: Optional[Reservation]) -> None:
    """Rend une réservation que la soumission n'a pas consommée (refus du fournisseur)."""
    if res is None:
        return
    try:
        db_cap.liberer(res.org_id, res.connector, res.units)
    except Exception:
        logger.exception("spend cap: libération impossible org=%s %s (%s unités)",
                         res.org_id, res.connector, res.units)
