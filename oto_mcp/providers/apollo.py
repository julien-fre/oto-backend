"""Déclaration de registre du connecteur `apollo`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "apollo", ["apollo"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=20, platform_key_open=True,
    label="Apollo.io",
    help="prospection B2B (organizations, people, job postings)",
    href="https://app.apollo.io",
)

CATEGORY = "Prospection"
PUBLISHER = "Apollo"
LOGO_DOMAIN = "apollo.io"
