"""Déclaration de registre du connecteur `posthog`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# posthog : analytics produit — HogQL, events, personnes, comptes (groupes),
# insights, feature flags, session recordings. byo-only.
# TROIS champs : la clé PERSONNELLE `phx_…` (la clé de PROJET `phc_…`, celle
# que PostHog met le plus en avant, est refusée par l'API de lecture — le
# client la rejette à la pose plutôt que de laisser un 401 illisible) ; le
# `host` régional, car us/eu sont deux déploiements distincts et une clé de
# l'un est inconnue de l'autre ; et un `project_id` facultatif qui épingle la
# clé sur UN projet (sinon découvert depuis la clé).
CONNECTOR = _c(
    "posthog", ["posthog"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="PostHog",
    help="analytics produit — requêtes HogQL, events, personnes, insights, "
         "feature flags, session recordings",
    href="https://posthog.com", credential_fields=(
        CredentialField("api_key", "Personal API key (phx_…)", secret=True, reveal=True,
                        help="PostHog → Settings → Personal API keys. PAS la clé de "
                             "projet `phc_…` du snippet JS, qui est refusée ici."),
        CredentialField("host", "Région / instance", secret=False, required=False,
                        help="https://us.posthog.com (défaut) ou "
                             "https://eu.posthog.com, ou l'URL de votre instance"),
        CredentialField("project_id", "Projet par défaut (optionnel)", secret=False,
                        required=False,
                        help="épingle la clé sur UN projet ; sinon résolu "
                             "automatiquement depuis la clé"),
    ),
)

CATEGORY = "Dev"
PUBLISHER = "PostHog"
LOGO_DOMAIN = "posthog.com"
