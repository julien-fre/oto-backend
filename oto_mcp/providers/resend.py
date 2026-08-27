"""Déclaration de registre du connecteur `resend`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# resend : credential-only (PAS de tools propres). La clé Resend de l'org est
# consommée par `email_send` (transport=resend) via resolve_api_key, cascade
# user > org. Domaine d'envoi vérifié côté Resend par l'org ; l'adresse `from`
# vit dans orgs.email_settings, pas dans le credential. Hors socle (pas un
# tool à exposer). tools/resend.py = register() no-op pour
# satisfaire l'invariant « un fichier tools/ par provider kind=tools ».
# resend : email transactionnel BYOK (clé Resend de l'ORG). byo_org uniquement
# (l'email est org-level) ; self_serve = dispo à la demande pour toute org. La
# propriété du domaine est garantie par Resend (la clé ne peut envoyer que depuis
# les domaines vérifiés dans le compte Resend de l'org) → zéro logique domaine côté oto.
CONNECTOR = _c(
    "resend", ["resend"], auth_modes={"byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Resend", help="envoi d'email transactionnel (clé de l'org)",
    publisher="Resend", href="https://resend.com",
)

LOGO_DOMAIN = "resend.com"
