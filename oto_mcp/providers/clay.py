"""Déclaration de registre du connecteur `clay`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# UNE carte, DEUX sortes d'entrées nommées (multi-compte dérivé de `fields`) :
#   · kind=api   — la clé Public API (routines, recherche, tables Enterprise).
#     Clé PERSONNELLE côté Clay (liée à un utilisateur et à ses crédits).
#   · kind=table — le webhook entrant d'UNE table Clay (source « Monitor webhook »),
#     seul chemin d'ÉCRITURE de lignes dans Clay. Autant d'entrées que de tables ;
#     le nom de l'entrée est le nom de la table que l'agent passe à `clay_push_rows`.
# `kind` vit dans `meta` (`in_meta`) : les tools listent les tables et trouvent
# l'entrée api SANS déchiffrer. Le discriminant masque les champs de l'autre sorte
# et `validate_fields` les écarte à l'écriture.
# Aucun endpoint ne crée un webhook de table : il se copie depuis l'UI Clay. Le champ
# `webhook` accepte donc l'URL OU la commande cURL que Clay affiche (URL + jeton en
# en-tête), relue par `oto.tools.clay.parse_curl` à la résolution.
# byo seul, pas de clé plateforme : chaque appel consomme les crédits Clay du client.
CONNECTOR = _c(
    "clay", ["clay"], auth_modes={"byo_user", "byo_org"}, secret_kind="fields",
    label="Clay",
    help="enrichissement GTM — routines, recherche people/companies, et écriture "
         "de lignes dans tes tables Clay (webhooks)",
    href="https://www.clay.com",
    account_noun="table",
    field_discriminator="kind",
    credential_fields=(
        CredentialField("kind", "Type", secret=False, choices=("table", "api"),
                        in_meta=True,
                        help="`table` = une table Clay où écrire des lignes (webhook) ; "
                             "`api` = ta clé Public API (routines, recherche)"),
        # `whitespace_significant` : une commande cURL collée VIT de ses espaces —
        # le nettoyage par défaut (retrait de tout blanc) la rendrait illisible.
        CredentialField("webhook", "Webhook URL ou commande cURL", secret=True,
                        when=("table",), whitespace_significant=True,
                        help="dans Clay : + Add → Monitor webhook, puis copie l'URL "
                             "ou la commande cURL entière (le jeton d'auth est repris)"),
        CredentialField("auth_token", "Jeton d'auth", secret=True, required=False,
                        when=("table",),
                        help="facultatif — seulement si tu as ajouté un jeton au webhook "
                             "et collé l'URL seule"),
        CredentialField("api_key", "API key", secret=True, when=("api",),
                        help="Settings → Account → API keys dans Clay"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Clay"
LOGO_DOMAIN = "clay.com"

DESCRIPTION = (
    "Tes tables Clay : ajoute chaque table par son webhook (colle la commande cURL "
    "que Clay affiche) et oto y écrit des lignes. Avec ta clé API Clay en plus : "
    "lancer tes routines (fonctions, workflows), chercher dans la base people/"
    "companies de Clay et lire tes tables (Enterprise). Chaque appel consomme tes "
    "crédits Clay."
)
