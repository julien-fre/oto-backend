"""Déclaration de registre du connecteur `supabase`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "supabase", ["supabase"], auth_modes={"byo_user"}, keyed=True,
    secret_kind="api_key", label="Supabase",
    help="Management API (projets, config auth, logs)",
    href="https://supabase.com",
)

CATEGORY = "Dev"
PUBLISHER = "Supabase"
LOGO_DOMAIN = "supabase.com"
