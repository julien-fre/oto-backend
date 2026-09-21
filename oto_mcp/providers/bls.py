"""Déclaration de registre du connecteur `bls`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# bls : salaires et emploi par métier aux États-Unis (enquête OEWS du Bureau of
# Labor Statistics), par l'API publique v2 — open data, AUCUN credential.
# ⚠️ Sans clé d'enregistrement l'API plafonne à 25 requêtes par JOUR, comptées sur
# l'adresse appelante : le plafond est donc partagé par toute la plateforme. La clé
# gratuite (500/jour) se pose côté exploitant, en variable d'env `BLS_API_KEY` que le
# client oto-core lit seul — ce n'est pas un credential d'utilisateur, d'où
# `secret_kind="none"` et pas de cascade.
CONNECTOR = _c(
    "bls", ["bls"], secret_kind="none",
    label="Salaires US (BLS)",
    help="salaires et emploi par métier aux États-Unis — distribution P10 à P90 par "
         "code SOC, au national, par État ou par aire métropolitaine (open data BLS OEWS)",
    href="https://www.bls.gov/oes/",
)

CATEGORY = "RH"
PUBLISHER = "U.S. Bureau of Labor Statistics"
DESCRIPTION = (
    "Les salaires et l'emploi par métier aux États-Unis, en open data du Bureau "
    "of Labor Statistics (enquête OEWS) : moyenne, médiane et percentiles P10 à "
    "P90 annuels pour un code SOC, au national, par État ou par aire "
    "métropolitaine. Dernière année publiée seulement."
)
LOGO_DOMAIN = "bls.gov"
