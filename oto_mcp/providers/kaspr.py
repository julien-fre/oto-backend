"""Déclaration de registre du connecteur `kaspr`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "kaspr", ["kaspr"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=5, platform_key_open=True,
    label="Kaspr", help="enrichissement", href="https://app.kaspr.io",
    # logo.dev sert une bannière marketing pour kaspr.io (pas la marque) →
    # override sur le favicon officiel (K blanc sur dégradé, 160×160).
    logo_url="https://www.kaspr.io/hubfs/2023%20-%20Kaspr%20Brand%20Logos/favicon.png",
)

CATEGORY = "Prospection"
PUBLISHER = "Kaspr"
LOGO_DOMAIN = "kaspr.io"
