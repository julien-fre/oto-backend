"""Déclaration de registre du connecteur `promptwatch`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# promptwatch : monitoring de visibilité IA (comment une marque apparaît dans
# les réponses ChatGPT/Claude/Gemini…) — prompts organisés en monitors,
# analytics visibilité/sentiment/citations, contenu généré par IA pour
# combler les gaps de couverture. Client REST synchrone dans oto-core
# (`oto.tools.promptwatch`), tools curés dans `tools/promptwatch.py` (10
# tools `op=`, ADR 0047 — la portée v1 couvre projects/monitors/prompts
# (+ bulk natif)/responses/visibility/citations/content+content-gap/
# tags+topics/personas/brands ; Publishing, Content Agent, Ads Radar,
# Shopping, Site Health, Sitemap, Page Tracker, Models, Actions, Query
# Fanouts et Social Citations sont DÉFÉRÉS, pas construits). Credential à
# 2 champs (clé API + project_id optionnel — ne sert qu'à une clé
# ORG-level ciblant un projet précis, une clé project-level l'ignore) →
# secret_kind="fields", résolu via resolve_credential_fields, même patron
# que lighton. BYO only : pas d'accord commercial Otomata↔PromptWatch.
CONNECTOR = _c(
    "promptwatch", ["promptwatch"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields",
    label="PromptWatch",
    help="monitoring de visibilité IA — prompts, monitors, réponses, "
         "citations, contenu généré pour combler les gaps",
    href="https://promptwatch.com", credential_fields=(
        CredentialField("api_key", "API key", secret=True,
                        help="Settings > API Keys sur le dashboard PromptWatch"),
        CredentialField("project_id", "Project ID par défaut", secret=False,
                        required=False,
                        help="clé ORG-level ciblant un projet précis "
                             "uniquement (optionnel) — voir promptwatch_project"),
    ),
)

CATEGORY = "Marketing"
PUBLISHER = "PromptWatch"
LOGO_DOMAIN = "promptwatch.com"
