"""Le point de lecture UNIQUE des droits déclarés (ADR 0070 §7).

Le cœur demande « cette personne, dans cette org, a-t-elle ce droit, à quelle valeur ? »
et ne sait rien de plus : ni qui l'a posé, ni s'il est payé. Il n'importe pas
`billing` — le commerce est un producteur de droits, qui écrit dans `org_entitlements`.

`value_for` applique la règle entière, relue à chaque usage (jamais mise en cache) :

1. les lignes VALIDES à l'instant (début inclus, fin exclue, borne nulle = non bornée) ;
2. de l'org ET de la personne dans l'org, toutes sources confondues ;
3. **le plus généreux gagne** : le maximum ;
4. aucune ligne → **le défaut déclaré par l'instance** (`OTO_ENTITLEMENT_DEFAULTS`),
   jamais un défaut du code. Une clé du catalogue sans défaut déclaré fait échouer le
   démarrage (`verifier_defauts`, appelé par `server.main`).

⚠️ Une ligne vaut même quand elle est MOINS généreuse que le défaut : le défaut ne
s'applique qu'en l'absence de toute ligne valide (un quota de 5 posé l'emporte sur un
défaut de 10).
"""
from __future__ import annotations

import functools
import json
import os
from datetime import datetime
from typing import Optional

from .. import entitlements_catalogue as catalogue
from .. import providers
from ..db import entitlements as db_entitlements

# Les noms que le reste du code importe d'ici (le catalogue en est la source).
PLATFORM_UNMETERED = catalogue.PLATFORM_UNMETERED
MEMBERS_MAX = catalogue.MEMBERS_MAX

_VAR_DEFAUTS_DROITS = "OTO_ENTITLEMENT_DEFAULTS"
# Dans la déclaration, « sans plafond » s'écrit en toutes lettres.
_SANS_PLAFOND_DECLARE = "unlimited"


def _declaration_defauts() -> str:
    brut = os.environ.get("OTO_ENTITLEMENT_DEFAULTS")
    if not brut:
        raise RuntimeError(
            f"{_VAR_DEFAUTS_DROITS} absente : l'instance doit déclarer la valeur de chaque droit du "
            "catalogue pour qui n'en a aucun posé (JSON, ex. "
            '{"unipile": 0, "platform_unmetered": 0, "unipile_seats": 5, '
            '"members_max": "unlimited", "platform_key:*": 0}). '
            "`scripts/defauts_des_droits.py` imprime ceux de l'instance historique.")
    return brut


@functools.lru_cache(maxsize=4)
def _analyser_defauts(brut: str) -> dict[str, int]:
    """La déclaration, vérifiée ENTIÈRE : JSON objet, chaque clé du catalogue (ou le joker
    `platform_key:*`), chaque valeur conforme au genre de sa clé, et chaque clé fixe du
    catalogue couverte. Sinon `RuntimeError` qui nomme tout ce qui manque."""
    try:
        donnees = json.loads(brut)
    except ValueError as e:
        raise RuntimeError(f"{_VAR_DEFAUTS_DROITS} n'est pas du JSON : {e}") from e
    if not isinstance(donnees, dict):
        raise RuntimeError(f"{_VAR_DEFAUTS_DROITS} doit être un objet JSON {{clé: valeur}}")
    defauts: dict[str, int] = {}
    fautes: list[str] = []
    for cle, valeur in donnees.items():
        if valeur == _SANS_PLAFOND_DECLARE:
            valeur = catalogue.SANS_PLAFOND
        try:
            defauts[cle] = catalogue.valeur_de_defaut(cle, valeur)
        except ValueError as e:
            fautes.append(str(e))
    manquantes = sorted(set(catalogue.FIXES) - set(defauts))
    if catalogue.PLATFORM_KEY_JOKER not in defauts:
        sans = sorted(c for c in providers.REGISTRY
                      if catalogue.platform_key(c) not in defauts)
        if sans:
            manquantes.append(f"{catalogue.PLATFORM_KEY_JOKER} (ou une surcharge pour : "
                              f"{', '.join(sans)})")
    if manquantes:
        fautes.append(f"défaut non déclaré pour : {', '.join(manquantes)}")
    if fautes:
        raise RuntimeError(f"{_VAR_DEFAUTS_DROITS} refusée — " + " ; ".join(fautes))
    return defauts


def defauts_de_l_instance() -> dict[str, int]:
    """Les défauts déclarés par l'instance, vérifiés. Lève si la déclaration manque ou
    ne couvre pas le catalogue."""
    return _analyser_defauts(_declaration_defauts())


def verifier_defauts() -> None:
    """La garde de démarrage : une clé du catalogue sans défaut déclaré est une erreur
    AU DÉMARRAGE, jamais un 0 silencieux au premier usage."""
    defauts_de_l_instance()


def defaut_du_droit(key: str) -> int:
    """Le défaut déclaré de `key` (clé du catalogue, sinon `ValueError`)."""
    catalogue.droit(key)
    defauts = defauts_de_l_instance()
    if key in defauts:
        return defauts[key]
    return defauts[catalogue.PLATFORM_KEY_JOKER]


def value_for(sub: Optional[str], org_id: Optional[int], key: str,
              now: Optional[datetime] = None) -> int:
    """La valeur du droit `key` pour la personne `sub` dans l'org `org_id` : le maximum
    des lignes valides à `now` (défaut : maintenant, horloge de la base) de l'org et de
    la personne, sinon le défaut déclaré par l'instance. `sub` None = l'org seule ;
    `org_id` None = aucune ligne ne peut s'appliquer, le défaut répond."""
    catalogue.droit(key)
    posee = (None if org_id is None
             else db_entitlements.max_value(int(org_id), sub, key, now))
    return defaut_du_droit(key) if posee is None else posee


def org_has(org_id: int, right_key: str) -> bool:
    """Vrai si l'ORG a ce droit oui/non (ou une valeur non nulle) : `value_for` sans
    personne, défaut d'instance compris."""
    return value_for(None, org_id, right_key) >= 1
