"""Déclaration de registre du connecteur `granola`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# granola : notes de réunion, transcripts, résumés IA, dossiers, journal
# d'audit, webhook endpoints. keyed api_key (Bearer), byo-only (pas de clé
# plateforme) — clé personnelle (tout membre Business) ou clé workspace
# (admin, Enterprise), toutes deux un Bearer simple ici.
CONNECTOR = _c(
    "granola", ["granola"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Granola",
    help="notes de réunion, transcripts, résumés IA, dossiers, audit, webhooks",
    href="https://granola.ai",
)

CATEGORY = "Knowledge"
PUBLISHER = "Granola"
LOGO_DOMAIN = "granola.ai"

DESCRIPTION = (
    "Les notes de réunion prises par Granola : transcripts, résumés générés par "
    "IA, dossiers, journal d'audit et endpoints de webhook. Clé personnelle "
    "(tout abonnement Business) ou clé workspace (admin, Enterprise)."
)
