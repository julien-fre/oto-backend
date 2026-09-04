"""Déclaration de registre du connecteur `stripe`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# stripe : paiements & facturation — le compte Stripe du client.
# byo-only (pas de clé plateforme) : ce sont ses livres de comptes, une clé
# partagée entre orgs n'aurait aucun sens.
# TROIS champs plutôt qu'`api_key` nu, parce que deux satellites NON secrets
# décident CE QUE la clé lit : `api_version` (une version épinglée qui
# diverge du compte change des formes de réponse en silence) et surtout
# `stripe_account` — avec Connect, la MÊME question rend le chiffre
# d'affaires d'une AUTRE société selon cet en-tête. En faire un champ de
# credential est la forme la plus forte de « jamais déduit par appel » : ce
# n'est un paramètre d'aucun tool, donc impossible à basculer en cours de
# conversation.
CONNECTOR = _c(
    "stripe", ["stripe"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Stripe",
    help="paiements & facturation — clients, abonnements, factures, encaissements, solde",
    href="https://stripe.com", credential_fields=(
        CredentialField("api_key", "Clé restreinte (rk_…) ou secrète (sk_…)",
                        secret=True,
                        help="Stripe Dashboard → Developers → API keys → « Create "
                             "restricted key » ; des permissions en LECTURE suffisent. "
                             "Une clé publiable `pk_…` est refusée : elle ne lit rien."),
        CredentialField("api_version", "Version d'API (optionnel)", secret=False,
                        required=False,
                        help="vide = la version par défaut du compte, celle que montre "
                             "son dashboard"),
        CredentialField("stripe_account", "Compte connecté (optionnel, Connect)",
                        secret=False, required=False,
                        help="acct_… — TOUTES les lectures portent alors sur ce "
                             "compte, pas sur le vôtre"),
    ),
)

CATEGORY = "Finance"
PUBLISHER = "Stripe"
LOGO_DOMAIN = "stripe.com"

DESCRIPTION = (
    "Les paiements et la facturation d'un compte Stripe : clients, abonnements, "
    "factures, encaissements et solde. Trois champs de credential : la clé "
    "secrète, une version d'API optionnelle, et l'identifiant de compte Connect "
    "si tu factures pour plusieurs sociétés depuis un seul compte."
)
