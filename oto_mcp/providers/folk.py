"""Déclaration de registre du connecteur `folk`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# folk : né APRÈS le coffre — pas de colonne legacy users.folk_api_key,
# le coffre connector_credentials est canonique. byo-only (pas de clé
# plateforme) ; compte partagé équipe = credential de l'org Otomata.
CONNECTOR = _c(
    "folk", ["folk"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Folk", help="CRM — contacts, companies, deals & custom objects",
    href="https://app.folk.app",
)

CATEGORY = "Prospection"
PUBLISHER = "Folk"
LOGO_DOMAIN = "folk.app"
