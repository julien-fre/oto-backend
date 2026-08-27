"""Déclaration de registre du connecteur `airtable`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# airtable : bases / tables / champs / lignes / commentaires / pièces jointes /
# sync CSV — toute la section « Base data » de la Web API, PLUS le schéma, sans
# lequel un agent ne peut pas écrire une ligne (il lui faut les noms et types de
# colonnes). keyed api_key (Bearer Personal Access Token), **byo-only** : un PAT
# Airtable porte à la fois des scopes ET une liste de bases nommément accordées —
# une clé plateforme exposerait les bases d'Otomata à toutes les orgs.
CONNECTOR = _c(
    "airtable", ["airtable"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Airtable",
    help="bases, tables, champs, lignes, commentaires, pièces jointes, sync CSV",
    href="https://airtable.com", credential_fields=(
        CredentialField("key", "Personal Access Token", secret=True,
                        help="airtable.com/create/tokens → créer un token, cocher "
                             "les scopes data.records:*, data.recordComments:* et "
                             "schema.bases:* PUIS ajouter les bases dans « Access » "
                             "(les scopes seuls ne donnent accès à aucune base)"),
    ),
)

CATEGORY = "Knowledge"
PUBLISHER = "Airtable"
DESCRIPTION = (
    "Les bases Airtable en lecture ET en écriture : lister et filtrer "
    "des lignes (formules, vues, tris), en créer, mettre à jour ou "
    "rapprocher par upsert, commenter, joindre des fichiers. Le "
    "schéma est exposé aussi (tables, champs, types et options), donc "
    "l'agent découvre les colonnes avant d'écrire dedans."
)
LOGO_DOMAIN = "airtable.com"
