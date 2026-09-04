"""Déclaration de registre du connecteur `lighton`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# lighton : indexation documentaire souveraine (API v3 api.lighton.ai —
# l'applicatif Paradigm et son API v2 sont dépréciés côté LightOn) :
# retrieval hybride multivectoriel (search), RAG groundé (ask), parse →
# Markdown, extraction structurée, ingestion par workspace (sync
# SharePoint/Drive possible côté console). Credential à 3 champs (clé API
# + base URL optionnelle instance privée + workspace_id par défaut —
# l'instance ADR 0038 devient « une clé × un workspace », bindable à un
# projet) → secret_kind="fields", résolu via resolve_credential_fields.
# BYO only : le compte LightOn appartient au client, pas d'accord
# plateforme.
CONNECTOR = _c(
    "lighton", ["lighton"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields",
    label="LightOn",
    help="indexation documentaire souveraine — search hybride + RAG groundé "
         "+ parse/extract (API v3)",
    href="https://lighton.ai", credential_fields=(
        CredentialField("api_key", "API key", secret=True,
                        help="créée sur console.lighton.ai"),
        CredentialField("base_url", "Instance URL", secret=False, required=False,
                        help="instance privée uniquement (défaut : SaaS "
                             "https://api.lighton.ai)"),
        CredentialField("workspace_id", "Workspace par défaut", secret=False,
                        required=False,
                        help="id du workspace LightOn qui scope par défaut "
                             "search/ask/upload (optionnel)"),
    ),
)

CATEGORY = "Knowledge"
PUBLISHER = "LightOn"
LOGO_DOMAIN = "lighton.ai"

DESCRIPTION = (
    "Indexation documentaire souveraine avec LightOn : recherche hybride "
    "multivectorielle, question-réponse groundée sur tes documents (RAG), "
    "conversion en Markdown, extraction structurée, et ingestion par espace de "
    "travail (synchronisation SharePoint/Drive possible depuis la console "
    "LightOn)."
)
