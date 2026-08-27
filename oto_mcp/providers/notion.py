"""Déclaration de registre du connecteur `notion`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "notion", ["notion"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Notion",
    help="pages, bases de données, blocs (lecture + écriture)",
    href="https://www.notion.so",
)

CATEGORY = "Knowledge"
PUBLISHER = "Notion"
LOGO_DOMAIN = "notion.so"
