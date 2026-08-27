"""Déclaration de registre du connecteur `greenhouse`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# Connecteurs de recrutement (Applicant Tracking Systems). byo keyed api_key
# (chacun pose sa clé Harvest/API key, cascade user > org), hors bundle (opt-in,
# activables par org/admin). Inertes tant que non activés en DB (deny-by-default,
# comme hubspot/apollo). Recruitee = credential à 2 champs (token + company id)
# → resolve_credential_fields, pas keyed.
CONNECTOR = _c(
    "greenhouse", ["greenhouse"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Greenhouse",
    help="ATS — candidats, jobs, candidatures, notes (Harvest API)",
    href="https://www.greenhouse.io",
)

CATEGORY = "Recrutement"
PUBLISHER = "Greenhouse"
LOGO_DOMAIN = "greenhouse.io"
