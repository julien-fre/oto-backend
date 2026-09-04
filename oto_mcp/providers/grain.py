"""Déclaration de registre du connecteur `grain`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# grain : enregistrements de réunion, transcripts, partage, webhooks,
# données d'organisation. keyed api_key (Bearer + header Public-Api-Version),
# byo-only (pas de clé plateforme) — Personal Access Token (par user) ou
# Workspace Access Token (admin, accès à toutes les données du workspace).
CONNECTOR = _c(
    "grain", ["grain"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Grain",
    help="enregistrements de réunion, transcripts, partage, webhooks, org",
    href="https://grain.com",
)

CATEGORY = "Knowledge"
PUBLISHER = "Grain"
LOGO_DOMAIN = "grain.com"

DESCRIPTION = (
    "Les réunions enregistrées par Grain : transcripts, partage, webhooks et "
    "données d'organisation. Jeton personnel ou jeton workspace (accès admin à "
    "toutes les données de l'espace)."
)
