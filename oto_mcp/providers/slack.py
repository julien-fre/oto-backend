"""Déclaration de registre du connecteur `slack`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# slack : messagerie. BYO 100% configurable par org/user (#25) — credential
# MULTI-CHAMPS (bot token xoxb- ET/OU user token xoxp-, au moins un requis),
# résolu via resolve_credential_fields (modèle silae/zoho, PAS keyed). byo_user
# OU byo_org (un workspace partagé par l'org = son bot token). Fallback de lecture
# du credential legacy (token unique pré-multichamps) dans tools/slack.py.
# MULTI-WORKSPACE (#409) : un compte du coffre = un workspace, puisqu'un token
# Slack est émis par installation de l'app dans un workspace. Choix à l'appel
# par `_account=` ; la lib `oto.tools.slack` sait déjà servir N workspaces.
CONNECTOR = _c(
    "slack", ["slack"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    personal_session=False, label="Slack", account_noun="workspace",
    help="messagerie Slack (bot token xoxb- et/ou user token xoxp-)",
    href="https://slack.com", credential_fields=(
        CredentialField("bot_token", "Bot token (xoxb-)", secret=True,
                        required=False),
        CredentialField("user_token", "User token (xoxp-)", secret=True,
                        required=False),
    ),
)

CATEGORY = "Comms"
PUBLISHER = "Slack"
LOGO_DOMAIN = "slack.com"
