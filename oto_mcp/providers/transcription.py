"""Déclaration de registre du connecteur `transcription`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# transcription : un audio du projet devient une page du projet (ADR 0074). Le
# fournisseur est Mistral (Voxtral) ; le connecteur porte le VERBE, pas la marque —
# `mistral` existe déjà comme porteur de clé SANS outil (`kind="credential"`, agents
# programmés), et un connecteur à outil ne s'y greffe pas.
#
# `byo_org` + `platform` : la clé est celle de l'organisation (c'est elle qui paie la
# minute d'audio) OU une instance plateforme accordée à une org (l'org qui n'a pas de
# clé propre, ex. un pilote : la clé, la langue et le vocabulaire sont alors ceux de
# l'instance plateforme). L'org qui a sa propre instance passe avant la plateforme
# (cascade). MONO-compte, déclaré : un appel
# transcrit avec UNE clé et UN vocabulaire, et deux instances posées sur la même org
# n'auraient aucun critère pour se départager — le rattachement à un projet passe par
# un slot (ADR 0035), comme toute instance.
#
# Deux champs NON secrets font de l'instance « une clé × une langue × un
# vocabulaire » (ADR 0074 D1) :
# - `language` : code de langue, `fr` quand il est vide ; `auto` = détection par
#   l'amont ;
# - `vocabulary` : termes métier saisis à la main, séparés par des virgules ou des
#   retours à la ligne — l'espace y est SIGNIFICATIF (« pompe à chaleur »), d'où
#   `whitespace_significant` : le nettoyage par défaut retirerait tous les blancs.
CONNECTOR = _c(
    "transcription", ["transcription"], auth_modes={"byo_org", "platform"},
    secret_kind="fields", cardinality="mono",
    label="Transcription (Mistral)",
    help="transcrit un audio du projet en page du projet — locuteurs distingués, "
         "vocabulaire de l'organisation",
    href="https://console.mistral.ai/api-keys",
    credential_fields=(
        CredentialField("api_key", "Clé API Mistral", secret=True,
                        help="créée sur console.mistral.ai"),
        CredentialField("language", "Langue", secret=False, required=False,
                        help="code de langue de l'audio (défaut : fr) ; « auto » = "
                             "détection automatique"),
        CredentialField("vocabulary", "Vocabulaire", secret=False, required=False,
                        whitespace_significant=True,
                        help="termes métier à bien orthographier (ouvrages, "
                             "matériaux, noms propres), séparés par des virgules ou "
                             "des retours à la ligne — 100 mots au plus"),
    ),
)

CATEGORY = "Knowledge"
PUBLISHER = "Mistral AI"
LOGO_DOMAIN = "mistral.ai"

DESCRIPTION = (
    "Transcription d'enregistrements audio (visites, réunions, notes vocales) avec "
    "Mistral Voxtral, hébergé dans l'UE : le fichier déposé sur un projet devient une "
    "page du projet, un paragraphe par tour de parole, avec le vocabulaire métier de "
    "l'organisation."
)
