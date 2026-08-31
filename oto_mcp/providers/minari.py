"""Déclaration de registre du connecteur `minari`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# minari : prospection téléphonique — journal d'appels transcrits (résumé IA,
# objections détectées), listes de contacts à composer, champs personnalisés,
# analytics d'équipe (taux de décroché, conversations, RDV pris).
# keyed api_key (Bearer), byo-only, PAS de clé plateforme : la clé se crée dans
# Settings → API & webhook et porte les droits de TOUTE l'entreprise — le
# journal d'appels d'un client est le sien, une clé partagée entre orgs n'aurait
# aucun sens (même principe que `stripe` et `fireflies`).
# ⚠️ La portée des endpoints n'est pas uniforme côté Minari : listes et contacts
# ne voient que la source import CSV, tandis qu'appels et analytics couvrent
# toutes les sources (CRM inclus). Le module de tools le dit dans ses réponses,
# c'est le piège n°1 du connecteur.
CONNECTOR = _c(
    "minari", ["minari"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Minari",
    help="prospection téléphonique — appels transcrits, objections, listes à "
         "composer, analytics d'équipe",
    href="https://minari.ai",
)

CATEGORY = "Prospection"
PUBLISHER = "Minari"
LOGO_DOMAIN = "minari.ai"
