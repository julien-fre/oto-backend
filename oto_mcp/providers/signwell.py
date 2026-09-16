"""Déclaration de registre du connecteur `signwell`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# signwell : signature électronique — documents envoyés pour signature, modèles,
# envois groupés, webhooks (26 opérations, couverture complète de l'API publique).
# Le client vit dans oto-core (`oto.tools.signwell`), les outils dans
# `tools/signwell.py` (documents, modèles) et `tools/signwell_envois.py` (envois
# groupés, webhooks, compte).
#
# **byo-only, par nature** : une clé SignWell agit au nom du compte qui l'a créée —
# c'est ce nom qui figure sur les invitations et dans la piste d'audit du document
# signé. Une clé plateforme enverrait des contrats au nom de quelqu'un d'autre.
#
# ⚠️ ENVOIE à de vraies personnes : un document envoyé, un rappel, un envoi groupé
# partent en courriel. Les outils créent un BROUILLON par défaut, et un envoi
# groupé est un aperçu tant que `dry_run=False` n'est pas passé.
CONNECTOR = _c(
    "signwell", ["signwell"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    modules=("signwell", "signwell_envois"),
    label="SignWell",
    help="signature électronique : envoyer un document à signer, suivre, récupérer le PDF signé",
    href="https://www.signwell.com",
    credential_fields=(
        CredentialField(
            "key", "Clé d'API SignWell", secret=True,
            help="SignWell → Settings → API → « Create API key ». La clé agit au nom "
                 "du compte qui la crée : les invitations partent sous ce nom."),
    ),
)

CATEGORY = "Métier"
PUBLISHER = "SignWell"
LOGO_DOMAIN = "signwell.com"

DESCRIPTION = (
    "La signature électronique avec SignWell, depuis ton assistant : envoyer un "
    "contrat ou un NDA à signer, placer les champs, suivre qui a signé, relancer, "
    "récupérer le PDF signé, travailler depuis des modèles ou en envoi groupé. "
    "Chacun pose sa propre clé : les documents partent au nom de son compte."
)
