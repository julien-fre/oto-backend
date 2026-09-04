"""Déclaration de registre du connecteur `fireflies`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# fireflies : transcripts de réunion, contrôle de réunion en direct, AskFred
# (Q&A IA), org (users/groupes/canaux/bites/analytics/audit). GraphQL (un seul
# endpoint POST), keyed api_key (Bearer), byo-only (pas de clé plateforme).
# Webhooks V1/V2 = dashboard-only chez Fireflies, aucune query/mutation
# GraphQL pour ça — volontairement absent de la surface MCP de ce connecteur.
CONNECTOR = _c(
    "fireflies", ["fireflies"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Fireflies",
    help="transcripts de réunion, réunion en direct, AskFred, org",
    href="https://fireflies.ai",
)

CATEGORY = "Knowledge"
PUBLISHER = "Fireflies.ai"
LOGO_DOMAIN = "fireflies.ai"

DESCRIPTION = (
    "Les réunions enregistrées par Fireflies : transcripts, contrôle d'une "
    "réunion en direct, questions posées à AskFred (Q&A IA sur le contenu), et "
    "les données d'organisation (utilisateurs, groupes, canaux, analytics)."
)
