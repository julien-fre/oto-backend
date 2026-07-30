"""Le geste « connecter » d'un connecteur — déclaré par son module, dérivé partout.

**Le problème que ça ferme.** Certains connecteurs ne s'obtiennent pas en collant des
champs : il faut un geste hors formulaire (consentement OAuth, session navigateur…).
Rien ne le DÉCLARAIT, alors chaque surface a compensé à sa façon — et toujours par le
NOM du connecteur. Le dashboard montait le widget de consentement derrière un
`['zoho','zohodesk','zohoanalytics'].includes(name)` ; Salesforce, qui a pourtant
exactement la même forme côté backend (capacité de démarrage, callback, les deux hooks
`status_hints`, la fabrique `oauth_flow`), n'y était simplement pas — donc pas de bouton,
et un client ne pouvait pas finir sa connexion. Ajouter un nom de plus aurait marché
cinq minutes et fait grossir la seule chose qu'il fallait supprimer.

**Ce que le seam garantit.** Un connecteur déclare son flux ICI, dans son propre module
(patron `connector_verify` / `status_hints`). Le catalogue en dérive un descripteur de
FORME — quels paramètres l'utilisateur doit fournir, comment s'appelle le geste — et le
front rend un formulaire générique + un bouton, sans jamais connaître un nom.

**Ce que le descripteur ne porte PAS, délibérément** : aucune URL, aucune clé de
capacité, aucun nom d'outil. `/api/connectors` est servi sans authentification ; un
descripteur qui publierait ses chemins internes ferait de la surface d'attaque un effet
de bord de la documentation. Le chemin est FIXE et connu du client
(`POST /api/me/connectors/{name}/connect`), le nom voyage en paramètre de chemin.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlowParam:
    """Une valeur que l'utilisateur doit fournir pour démarrer le flux.

    `options` non vide ⟹ liste fermée (le front rend un select). C'est le DOMICILE
    UNIQUE de ces valeurs : la région Zoho était jusqu'ici recopiée quatre fois, dont
    une version fausse dans le libellé du registre (un `sa` que le code rejette)."""
    name: str
    label: str
    options: tuple[tuple[str, str], ...] = ()      # (valeur, libellé)
    default: str = ""
    required: bool = True
    help: str = ""

    def describe(self) -> dict:
        return {
            "name": self.name, "label": self.label, "required": self.required,
            "default": self.default, "help": self.help,
            "options": [{"value": v, "label": lbl} for v, lbl in self.options],
        }


@dataclass(frozen=True)
class Flow:
    connector: str
    start: Callable[..., dict]       # (ctx, values) -> {"auth_url": …}
    params: tuple[FlowParam, ...] = field(default_factory=tuple)
    label: str = "Connecter"


_FLOWS: dict[str, Flow] = {}


def declare(connector: str, *, start: Callable[..., dict],
            params: tuple[FlowParam, ...] = (), label: str = "Connecter") -> None:
    """Déclare le flux de connexion de ce connecteur. Appelé au niveau MODULE (comme
    `status_hints.register_state`) : c'est une déclaration pure, elle doit être lisible
    dès l'import, sans attendre le montage FastMCP."""
    for p in params:
        if not p.options and p.required and not p.default:
            # Un choix fermé sans options est indémarrable côté front : il rendrait un
            # select vide. Mieux vaut le refuser à la déclaration qu'au clic.
            raise ValueError(
                f"{connector}.{p.name} : paramètre requis sans options ni défaut.")
    _FLOWS[connector] = Flow(connector=connector, start=start,
                             params=tuple(params), label=label)


def supports(connector: str) -> bool:
    return connector in _FLOWS


def entries() -> dict[str, Flow]:
    return dict(_FLOWS)


def describe(connector: str) -> Optional[dict]:
    """Le champ `connect` du catalogue : la FORME du geste, rien d'autre.

    `None` pour les ~56 connecteurs qui n'ont pas de flux — le front lit alors son
    formulaire de champs habituel, comme avant."""
    f = _FLOWS.get(connector)
    if f is None:
        return None
    return {"label": f.label, "params": [p.describe() for p in f.params]}


def start(connector: str, ctx, values: dict) -> dict:
    """Démarre le flux déclaré. Lève `KeyError` si le connecteur n'en a pas — l'appelant
    (la capacité générique) le traduit en refus actionnable."""
    return _FLOWS[connector].start(ctx, values or {})
