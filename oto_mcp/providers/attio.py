"""Déclaration de registre du connecteur `attio`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# attio : hors socle (2026-06-11) — le MCP Attio officiel est meilleur pour
# l'instant. Code conservé (tools/attio.py) pour d'éventuelles implems
# custom ; installable depuis la library.
CONNECTOR = _c(
    "attio", ["attio"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", default_quota=200,
    label="Attio", help="CRM", href="https://app.attio.com",
)

CATEGORY = "Prospection"
PUBLISHER = "Attio"
LOGO_DOMAIN = "attio.com"

DESCRIPTION = (
    "Un CRM léger et personnalisable : lister, créer et mettre à jour des "
    "enregistrements (personnes, entreprises, deals) selon le schéma propre à "
    "ton espace Attio. Hors socle depuis juin 2026 — le MCP officiel d'Attio "
    "est aujourd'hui plus complet ; ce connecteur reste disponible pour des "
    "implémentations sur mesure."
)
