"""Déclaration de registre du connecteur `ashby`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "ashby", ["ashby"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Ashby",
    help="ATS — candidates, jobs, applications, notes",
    href="https://www.ashbyhq.com",
)

CATEGORY = "Recrutement"
PUBLISHER = "Ashby"
LOGO_DOMAIN = "ashbyhq.com"
