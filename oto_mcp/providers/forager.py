"""Déclaration de registre du connecteur `forager`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# forager : job posts + firmographics + people/contact enrichment, payant
# au crédit par lookup. Auth = header `X-API-KEY` plat (un seul secret),
# mais PAS `secret_kind="api_key"` : chaque appel datastorage a besoin en
# plus d'un `account_id` entier en path, résolu au runtime via `GET
# /api/users/current/` → `accounts[]` — donc modèle multi-champs (ADR
# 0011, `secret_kind="fields"`) même si le secret lui-même est un simple
# bearer, pour porter ce second champ (non-secret). `account_id` est
# FACULTATIF : `ForagerClient` le résout tout seul si la clé n'a accès
# qu'à un compte, et REFUSE (au lieu de deviner) si elle en a plusieurs —
# deviner facturerait potentiellement le mauvais compte. byo-only (compte
# payant du client, pas de pool de crédits partagé). Pas de tool de
# gestion de clé API (create/delete) — dashboard-only, cf. tools/forager.py.
CONNECTOR = _c(
    "forager", ["forager"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Forager", help="job posts, firmographics et enrichissement contacts (payant au crédit)",
    publisher="Forager.ai", href="https://forager.ai", credential_fields=(
        CredentialField("api_key", "Clé API (X-API-KEY)", secret=True),
        CredentialField(
            "account_id", "Account ID", secret=False, required=False,
            help="laisse vide sauf si ta clé a accès à plusieurs comptes Forager — "
                 "sinon résolu automatiquement"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Forager.ai"
LOGO_DOMAIN = "forager.ai"

DESCRIPTION = (
    "Offres d'emploi, données d'entreprise (firmographics) et enrichissement de "
    "contact chez Forager, payant au crédit par recherche. Une clé donne accès "
    "à un ou plusieurs comptes Forager ; le bon compte se résout tout seul "
    "quand elle n'en a qu'un."
)
