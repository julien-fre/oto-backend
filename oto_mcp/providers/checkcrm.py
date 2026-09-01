"""Déclaration de registre du connecteur `checkcrm`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# checkcrm : wrappe l'API v1 de l'app "enrichment" d'un partenaire (job-change check
# LinkedIn + gestion des subsidiaries) — byo par défaut, chaque org configure sa
# propre clé enrichment (voir enrichment/docs/sf-api.md) ; clé plateforme
# GRANT-ONLY depuis le 26/08 (#405). Nommé "checkcrm" (un
# seul token, pas de underscore) : namespace_of prend le 1er token avant "_",
# "check_crm" romprait le préfixe des tools check_crm_* (résoudrait "check").
CONNECTOR = _c(
    "checkcrm", ["checkcrm"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    label="Check CRM", help="job-change check + subsidiaries (enrichment API)",
    href="https://enrichment-two.vercel.app",
)

SANS_LOGO_DE_MARQUE = True
