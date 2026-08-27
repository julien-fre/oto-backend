"""Déclaration de registre du connecteur `teamtailor`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "teamtailor", ["teamtailor"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Teamtailor",
    help="ATS — candidats, jobs, candidatures (JSON:API)",
    href="https://www.teamtailor.com",
)

CATEGORY = "Recrutement"
PUBLISHER = "Teamtailor"
LOGO_DOMAIN = "teamtailor.com"
