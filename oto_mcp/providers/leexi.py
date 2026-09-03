"""Déclaration de registre du connecteur `leexi`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# leexi : intelligence conversationnelle (appels, transcripts, notes de réunion)
# — même famille que `fireflies`, `grain` et `granola`, d'où la catégorie
# Knowledge et le même régime BYO.
#
# ⚠️ Auth **Basic** à DEUX champs (`KEY_ID` + `KEY_SECRET`), pas une clé unique.
# D'où `secret_kind="fields"` et **pas** `keyed` : la résolution passe par
# `access.resolve_credential_fields`, qui rend les deux champs (`resolve_api_key`
# n'en rendrait qu'un). Les deux se génèrent côté Leexi dans Settings → Company
# Settings → API Keys, et demandent un compte ADMIN — ce n'est pas une clé qu'un
# utilisateur ordinaire peut se créer.
#
# `key_id` est déclaré NON secret : c'est l'identifiant que Leexi affiche dans sa
# liste de clés, et le rendre lisible permet de voir LAQUELLE est posée sans
# jamais exposer le secret qui va avec — même partage que `posthog` entre sa clé
# et son hôte.
#
# ⚠️ **Une clé neuve ne porte que `read_calls`.** Tout le reste — et nommément
# `write_users`/`write_teams`, qui engagent les LICENCES FACTURÉES du client —
# doit être accordé explicitement par un admin Leexi. Le connecteur ne peut pas
# contourner ce cran et n'essaie pas : les outils d'écriture existent, l'amont
# répond 403 si la clé ne les porte pas, et le message le dit.
#
# BYOK strict (`byo_user` + `byo_org`, aucun mode plateforme) : ce sont les
# conversations enregistrées du client, il ne peut pas y avoir de clé oto
# partagée qui donnerait à l'un les appels de l'autre.
CONNECTOR = _c(
    "leexi", ["leexi"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields",
    credential_fields=(
        CredentialField(
            "key_id", "API Key ID", secret=False,
            help="Identifiant de la clé, généré dans Leexi → Settings → "
                 "Company Settings → API Keys (compte admin requis)."),
        CredentialField(
            "key_secret", "Key Secret", secret=True,
            help="Le secret associé, montré UNE seule fois à la création de "
                 "la clé côté Leexi."),
    ),
    label="Leexi",
    help="appels et réunions enregistrés, transcripts, notes, équipes — "
         "l'intelligence conversationnelle de l'organisation",
    href="https://www.leexi.ai",
)

CATEGORY = "Knowledge"
PUBLISHER = "Leexi"
LOGO_DOMAIN = "leexi.ai"
