"""Déclaration de registre du connecteur `topograph`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# topograph : KYB — données + documents normalisés de 100+ registres publics
# européens via une seule API REST. byo par défaut (pay-per-request ; clé d'org
# partageable), keyed api_key (en-tête x-api-key résolu côté client) ; clé
# plateforme GRANT-ONLY depuis le 26/08 (#405). Hors socle : opt-in.
CONNECTOR = _c(
    "topograph", ["topograph"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Topograph",
    help="KYB — données & documents entreprise (registres européens)",
    href="https://www.topograph.co",
)

CATEGORY = "Prospection"
LOGO_DOMAIN = "topograph.co"
