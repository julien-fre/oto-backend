"""Déclaration de registre du connecteur `brevo`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# brevo : API PUBLIQUE v3 (`api.brevo.com/v3`, header `api-key`). Une clé porte
# tout le compte (pas de scope) → byo. Ne PAS confondre avec `brevoauto`
# (automations, session navigateur) : surfaces disjointes, credentials distincts.
CONNECTOR = _c(
    "brevo", ["brevo"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Brevo",
    help="emailing & CRM (contacts, listes, transactionnel, campagnes, deals)",
    publisher="Brevo", href="https://app.brevo.com",
    # 2 modules, 1 namespace : le CRM natif est un sous-domaine distinct, sorti
    # pour tenir la taille de fichier. `brevo_crm_*` → namespace_of = `brevo`.
    modules=("brevo", "brevo_crm"),
)

CATEGORY = "Prospection"
LOGO_DOMAIN = "brevo.com"
