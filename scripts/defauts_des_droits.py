#!/usr/bin/env python3
"""Imprime les défauts ACTUELS des droits déclarés de l'instance historique, en JSON —
de quoi amorcer `OTO_ENTITLEMENT_DEFAULTS` (ADR 0070 §7, oto-backend#1066).

Dérivés, jamais recopiés, de ce que le code applique aujourd'hui à qui n'a aucun droit
posé :

- `unipile`, `platform_unmetered` : `0` — sans droit, ni messagerie hébergée ni quotas
  levés ;
- `unipile_seats` : le plafond par défaut de comptes de messagerie
  (`unipile_connect._default_limit`, qui lit `OTO_MCP_UNIPILE_DEFAULT_LIMIT` ; son `0`
  « pas de plafond » s'écrit `unlimited`) ;
- `members_max` : `unlimited` — le cœur ne plafonne pas les membres ;
- `platform_key:*` : `0` — sans droit, pas d'accès à nos clés de plateforme ;
- `platform_key:<connecteur>` pour chaque connecteur ouvert sans droit
  (`platform_key_open` du registre) : son quota par jour (`quotas.quota_for`, qui lit
  `default_quota` et sa surcharge `OTO_MCP_QUOTA_<P>_DAILY` ; son `0` « illimité »
  s'écrit `unlimited`).

Le registre reste la règle appliquée tant qu'aucun lecteur ne passe par
`access.entitlements.value_for` pour ces clés : ce script ne fait que la TRADUIRE.
Il n'écrit rien. Usage : `python -m scripts.defauts_des_droits`.
"""
from __future__ import annotations

import json

from oto_mcp import entitlements_catalogue as catalogue
from oto_mcp import providers, unipile_connect
from oto_mcp.access import quotas

_SANS_PLAFOND = "unlimited"


def _nombre(n: int) -> "int | str":
    return _SANS_PLAFOND if n == 0 else n


def defauts() -> dict:
    sortie: dict = {
        catalogue.UNIPILE: 0,
        catalogue.PLATFORM_UNMETERED: 0,
        catalogue.UNIPILE_SEATS: _nombre(unipile_connect._default_limit()),
        catalogue.MEMBERS_MAX: _SANS_PLAFOND,
        catalogue.PLATFORM_KEY_JOKER: 0,
    }
    for nom in sorted(providers.REGISTRY):
        if providers.REGISTRY[nom].platform_key_open:
            sortie[catalogue.platform_key(nom)] = _nombre(quotas.quota_for(nom))
    return sortie


if __name__ == "__main__":
    print(json.dumps(defauts(), ensure_ascii=False, separators=(",", ":")))
