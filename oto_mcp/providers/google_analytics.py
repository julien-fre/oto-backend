"""Déclaration de registre du connecteur `google_analytics`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# google_analytics : GA4 en LECTURE, par CLÉ DE COMPTE DE SERVICE — pas par
# l'OAuth du connecteur `google`. Le consentement d'un utilisateur au scope
# `analytics.readonly` est bloqué par Google pour notre application (constaté le
# 25/09/2026) ; un compte de service ajouté comme Lecteur d'une propriété GA4
# lit sans le compte Google de personne, et se coupe en lui retirant l'accès.
#
# UN champ secret : le fichier JSON de la clé, entier (`secret_kind="fields"`,
# résolu par `access.resolve_credential_fields`). ⚠️ `whitespace_significant` :
# la clé privée PEM contient des espaces (« BEGIN PRIVATE KEY ») — le nettoyage
# par défaut, qui retire TOUS les blancs, la rendrait illisible. Seuls les bords
# sont strippés.
#
# byo_org d'abord : le cas fondateur est une org dont TOUS les agents lisent la
# même propriété, sans copier la clé chez chacun (instance d'org). byo_user reste
# ouvert pour une personne qui a son propre compte de service. Hors socle →
# installable à la demande. Namespace `ga4`, le nom que l'agent lit dans ses
# outils ; le connecteur garde le nom du produit.
CONNECTOR = _c(
    "google_analytics", ["ga4"], auth_modes={"byo_org", "byo_user"},
    secret_kind="fields", label="Google Analytics 4",
    help="audience et événements GA4 (lecture) — rapports, temps réel, "
         "dimensions et métriques, par compte de service",
    href="https://analytics.google.com", credential_fields=(
        CredentialField(
            "service_account_json", "Clé JSON du compte de service", secret=True,
            whitespace_significant=True,
            help="Google Cloud → IAM → Comptes de service → Clés → Ajouter une clé → "
                 "JSON : colle le contenu ENTIER du fichier. Puis, dans GA4, ajoute "
                 "l'email du compte de service comme Lecteur de la propriété."),
    ),
)

CATEGORY = "Marketing"
PUBLISHER = "Google"
LOGO_DOMAIN = "analytics.google.com"

DESCRIPTION = (
    "L'audience et les événements d'une propriété Google Analytics 4, en "
    "lecture : rapports sur une période, temps réel, catalogue des dimensions et "
    "métriques, événements clés. Accès par un compte de service ajouté comme "
    "Lecteur dans GA4 — aucun compte Google personnel n'est connecté."
)
