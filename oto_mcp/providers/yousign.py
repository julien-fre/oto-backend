"""Déclaration de registre du connecteur `yousign`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# byo (user OU org), résolu via `resolve_credential(want="byo")` comme forager :
# chacun connecte SA propre clé Yousign — PAS de clé plateforme partagée (une clé
# agit au nom du compte qui l'a créée). Deux champs : la clé, et l'environnement.
# Yousign a DEUX HÔTES (sandbox, production) et une clé de l'un est refusée par
# l'autre : l'environnement se DÉCLARE à la pose, il ne se devine pas. Écrit
# réellement (envoie une demande de signature à des tiers) : à la différence de
# gocardless (lecture seule), l'activation notifie des personnes réelles.
CONNECTOR = _c(
    "yousign", ["yousign"], availability="self_serve",
    auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Yousign", help="signature électronique (demandes, statut, document signé)",
    credential_fields=(
        CredentialField(
            "key", "Clé d'API Yousign", secret=True,
            help="Yousign → Développeurs → Clés d'API. La clé agit au nom du compte "
                 "qui la crée : les invitations partent sous ce nom."),
        CredentialField(
            "environment", "Environnement", secret=False, required=False,
            choices=("production", "sandbox"),
            help="« sandbox » pour une clé du bac à sable Yousign ; vide ou "
                 "« production » sinon. Une clé d'un environnement est refusée "
                 "par l'autre."),
    ),
)

CATEGORY = "Documents & signature"
PUBLISHER = "Yousign"
LOGO_DOMAIN = "yousign.com"

DESCRIPTION = (
    "Signature électronique : créer une demande de signature à partir d'un "
    "PDF avec ses signataires, l'activer (envoie les invitations), suivre son "
    "statut et récupérer le document signé. Chacun connecte sa propre clé "
    "Yousign, en production ou dans le bac à sable (à préciser à la pose) — pas "
    "de clé plateforme partagée."
)
