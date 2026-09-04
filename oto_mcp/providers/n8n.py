"""Déclaration de registre du connecteur `n8n`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# Connecteurs vers les plateformes d'automatisation tierces. byo, hors socle
# (opt-in, installables depuis la library), pas de clé plateforme (chacun
# pose la sienne). Inertes tant que non activés en DB (deny-by-default, comme hubspot).
# n8n / make : credential à 2 champs (clé + base URL de l'instance/zone —
# self-hosting & régionalisation imposent une URL propre) → secret_kind="fields",
# résolu via resolve_credential_fields. zapier : clé simple (AI Actions API),
# keyed → resolve_api_key.
CONNECTOR = _c(
    "n8n", ["n8n"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="n8n",
    help="automatisation de workflows — workflows + exécutions (API publique)",
    href="https://n8n.io", credential_fields=(
        CredentialField("api_key", "API key", secret=True),
        CredentialField("base_url", "Instance URL", secret=False,
                        help="ex. https://acme.app.n8n.cloud"),
    ),
)

CATEGORY = "Automatisation"
PUBLISHER = "n8n"
LOGO_DOMAIN = "n8n.io"

DESCRIPTION = (
    "Les workflows n8n : lister, déclencher et suivre l'exécution des workflows "
    "de ton instance (cloud ou self-hosted), via l'API publique. Deux champs : "
    "la clé API et l'URL de ton instance — n8n s'auto-héberge, il n'y a pas "
    "d'endpoint unique."
)
