"""« Ce compte fédéré est-il lié ? » — déclaré par le module qui détient le credential.

**Le trou que ça ferme.** `access.status_for` remplit `me.providers[…]` par TROIS boucles :
les connecteurs keyés (`db.KEY_PROVIDERS`), ceux à champs (`secret_fields`), et ceux à
session navigateur (`secret_kind == "cookie"`). Les connecteurs à credential OAuth
FÉDÉRÉ — atlassian, folkmcp, google — ne sont dans aucune : `keyed=False`,
`secret_fields=0`, `secret_kind='oauth'`. Ils n'avaient donc **aucune entrée**, et les
conséquences en cascade n'étaient connues de personne :

- la décoration `pending_action` itère les entrées existantes → un hook `status_hints`
  sur ces connecteurs aurait été **physiquement inatteignable** ;
- `health_ko` idem ;
- le verdict de la fiche (`connectorVerdict`, dashboard) lit `me.providers[name]` → il
  n'avait rien à lire ;
- **et c'est POURQUOI le front avait des noms de connecteurs dans ses URLs** :
  `ConnectorFederatedWidget` appelle `/api/<name>/oauth/status` parce qu'il n'a pas
  d'état à lire dans `/api/me`. Le nom-dans-l'URL n'était pas une négligence de style,
  c'était le contournement de ce trou.

**Pourquoi un seam plutôt qu'une quatrième boucle qui lit le coffre.** Les trois ne
rangent pas leur credential au même endroit : atlassian et folkmcp écrivent au scope
LEGACY `("user", sub)`, google écrit une ligne PAR COMPTE (`account = email`) avec ses
satellites dans `meta`. Une boucle générique qui irait lire le coffre elle-même se
tromperait sur au moins l'un des trois, silencieusement. Chaque module sait, et le dit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkState:
    """Ce que le connecteur sait dire de son lien, dans le vocabulaire du CONSOMMATEUR.

    Volontairement pauvre : `status_for` le traduit ensuite en `ProviderStatus` (la forme
    que le dashboard lit). Un module ne doit pas avoir à connaître ce contrat-là."""
    linked: bool
    set_at: Optional[str] = None
    accounts: int = 0          # multi-compte (google) : combien de comptes liés


_READERS: dict[str, Callable[[str], LinkState]] = {}


def register(connector: str, read: Callable[[str], LinkState]) -> None:
    """Déclare comment lire l'état de lien de ce connecteur. Appelé au niveau MODULE,
    comme `status_hints.register_state` : c'est une déclaration pure."""
    _READERS[connector] = read


def has(connector: str) -> bool:
    return connector in _READERS


def entries() -> tuple[str, ...]:
    return tuple(_READERS)


def state(connector: str, sub: str) -> Optional[LinkState]:
    """État de lien, ou `None` si le connecteur n'en déclare pas / si la lecture casse.

    Fail-open : `/api/me` ne doit JAMAIS tomber parce qu'un fournisseur tiers tousse.
    Un `None` rend l'entrée absente — exactement l'état d'avant ce module, donc une
    dégradation et pas une régression."""
    read = _READERS.get(connector)
    if read is None:
        return None
    try:
        return read(sub)
    except Exception:  # noqa: BLE001
        logger.warning("connector_link: lecture %s en échec (fail-open)", connector,
                       exc_info=True)
        return None
