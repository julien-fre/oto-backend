"""Déclaration de registre du connecteur `hubspot`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# byo keyed api_key, hors socle (opt-in, installables depuis la library), pas de
# clé plateforme (chacun pose la sienne). Inertes tant que non activés en DB
# (connector_activation, deny-by-default), comme foncier/sante.
CONNECTOR = _c(
    "hubspot", ["hubspot"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="HubSpot",
    help="CRM (contacts, companies, deals, tickets, notes, listes/segments, propriétés)",
    href="https://app.hubspot.com",
)

CATEGORY = "Prospection"
PUBLISHER = "HubSpot"
LOGO_DOMAIN = "hubspot.com"
