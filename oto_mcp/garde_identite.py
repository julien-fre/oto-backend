"""La garde d'identité : le point unique où une identité établie peut encore être REFUSÉE.

Toute requête qui agit sous un compte traverse UNE fois cette fonction, avec le sub
CANONIQUE (après le drain d'alias), à chaque requête et sans cache :

| face | porte |
|---|---|
| REST, jeton `oto_` (API ou délégation) | `api.base._authenticate`, branche haute |
| REST, JWT (tableau de bord, quel que soit l'émetteur) | `api.base._authenticate`, branche basse |
| MCP, toute requête (JWT OAuth ou jeton `oto_`) | `AccountSuspendedMiddleware.on_request` |
| lien d'upload signé (porte le sub scellé) | `api.uploads._do_signed_upload` |

Deux refus, dans cet ordre : le **tenant désactivé** (`tenant_desactive`, l'espace entier),
puis le **compte en pause** (`account_suspension`, la personne). Les deux prédicats ne
s'appellent nulle part ailleurs — `tests/test_garde_identite_chemins.py` énumère les
chemins et fige les appelants.

Ce qui n'y passe pas, et pourquoi : les secrets de MACHINE (worker `otow_`, identité de
service) ne sont pas des comptes ; l'endpoint MCP anonyme d'un projet publié n'a pas de
sub.
"""
from __future__ import annotations

from typing import Optional

from . import account_suspension, tenant_desactive


def refus(sub: Optional[str]) -> Optional[tuple[str, str]]:
    """`(code, message)` si l'identité `sub` ne doit plus être servie, `None` sinon.

    Ne rattrape rien : une panne de lecture REMONTE (fail-closed), comme les deux
    prédicats qu'elle compose."""
    if (coupe := tenant_desactive.refus(sub)):
        return tenant_desactive.CODE, coupe[0]
    if (pause := account_suspension.refus(sub)):
        return account_suspension.CODE, pause[0]
    return None
