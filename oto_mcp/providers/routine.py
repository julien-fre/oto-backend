"""Déclaration de registre du connecteur `routine`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# routine : déclencher une ROUTINE Claude Code (agent autonome hébergé chez
# Anthropic) par son endpoint `/fire`. oto ne fait pas tourner l'agent — il le
# déclenche, et l'agent revient sur `/mcp` avec les outils de son compte.
#
# UNE INSTANCE = UNE ROUTINE (ADR 0038 B5, même patron que « une clé × un
# workspace » chez lighton) : le jeton `/fire` est scopé par Anthropic à UNE
# routine, donc une automatisation = une instance, révocable seule et bindable à
# un projet. Un jeton unique qui déclencherait une routine « à tout faire »
# perdrait exactement le cran de sécurité qui rend ce chemin intéressant.
#
# byo only : la routine appartient à un compte claude.ai (elle n'est pas un objet
# d'org côté Anthropic, et ce qu'elle fait apparaît sous cette identité). Pas de
# clé plateforme : il n'y a rien à mutualiser, chaque automatisation a son jeton.
CONNECTOR = _c(
    "routine", ["routine"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields",
    label="Routine Claude Code",
    help="déclenche une routine Claude Code (agent autonome) — une instance par "
         "automatisation",
    publisher="Anthropic", href="https://claude.ai/code/routines",
    credential_fields=(
        CredentialField(
            "routine_id", "ID de la routine", secret=False, reveal=True,
            help="visible dans l'URL de la routine sur claude.ai/code/routines "
                 "(commence par `trig_`)"),
        CredentialField(
            "token", "Jeton de déclenchement", secret=True,
            help="généré dans le déclencheur API de la routine — affiché UNE "
                 "fois, non récupérable ensuite"),
    ),
)

LOGO_DOMAIN = "anthropic.com"
