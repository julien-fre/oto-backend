"""Déclaration de registre du connecteur `github`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# github : dépôts et code, issues, pull requests, organisations et Actions.
# Catégorie Dev, à côté de `posthog` et `supabase`.
#
# **Deux champs, un seul secret.** Le jeton, et une `base_url` NON secrète qui
# n'existe que pour GitHub Enterprise Server (`https://<host>/api/v3`) — laissée
# vide, c'est `api.github.com`. Elle est déclarée ici plutôt que devinée, parce
# qu'un client on-premise ne peut pas se servir du connecteur sans elle, et
# qu'une seconde carte « github enterprise » aurait dupliqué tout le reste.
#
# ⚠️ **Ce que le jeton peut faire ne se lit PAS dans cette fiche** : un jeton
# classique porte des *scopes* (`repo`, `read:org`, `workflow`…), un jeton
# « fine-grained » porte des permissions ET une liste de dépôts. Le connecteur
# ne peut ni les vérifier à la pose ni les élargir — la sonde de connexion
# rapporte donc ce que le jeton VOIT, et c'est tout ce qu'on peut promettre.
#
# ⚠️ Piège de diagnostic à connaître avant de lire un ticket support : sur une
# ressource privée hors portée du jeton, GitHub répond **404, pas 403**, exprès
# pour ne pas divulguer son existence. « Dépôt introuvable » veut donc presque
# toujours dire « jeton sans le droit », pas « nom mal orthographié ».
#
# BYOK strict (`byo_user` + `byo_org`, aucun mode plateforme) : un jeton GitHub
# porte l'identité de son porteur — chaque commit, chaque commentaire, chaque
# fusion est attribué à LUI. Une clé oto partagée signerait les écritures d'une
# org au nom d'un compte qui n'est pas le sien, ce qui n'a pas de sens ici.
# Multi-champs, donc `secret_kind="fields"` et **pas** `keyed` : la résolution
# passe par `access.resolve_credential_fields` (byo pur, sans palier plateforme
# ni quota), exactement comme `posthog` — même forme, un secret plus un réglage
# d'instance non secret. `keyed=True` irait chercher `resolve_api_key`, qui ne
# rend qu'UNE valeur et perdrait la `base_url`.
CONNECTOR = _c(
    "github", ["github"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields",
    credential_fields=(
        CredentialField(
            "token", "Jeton d'accès", secret=True,
            help="Jeton personnel (classique ou fine-grained), ou jeton "
                 "d'installation d'app. Ses scopes décident de ce que le "
                 "connecteur peut lire et écrire."),
        CredentialField(
            "base_url", "URL de l'API (Enterprise Server)", secret=False,
            required=False,
            help="À laisser VIDE pour github.com. Pour un GitHub Enterprise "
                 "Server auto-hébergé : https://<votre-hôte>/api/v3"),
    ),
    label="GitHub",
    help="dépôts et code, issues, pull requests, revues, organisations et "
         "équipes, exécutions GitHub Actions",
    href="https://github.com",
)

CATEGORY = "Dev"
PUBLISHER = "GitHub"
LOGO_DOMAIN = "github.com"

DESCRIPTION = (
    "Les dépôts d'une organisation ou d'un compte GitHub : code, issues, pull "
    "requests et leurs revues, organisations et workflows Actions. Jeton "
    "personnel classique, fine-grained ou jeton GitHub App, chacun avec ses "
    "propres scopes — ce que le jeton peut faire ne se lit pas sur cette fiche. "
    "Support GitHub Enterprise Server via une URL de base dédiée."
)
