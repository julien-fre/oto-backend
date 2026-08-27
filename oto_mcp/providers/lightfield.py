"""Déclaration de registre du connecteur `lightfield`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# lightfield : CRM dont le modèle de champs est PROPRE À CHAQUE WORKSPACE (les
# clés sont définies par le client, pas par l'éditeur) — d'où un `op="definitions"`
# sur chaque objet, et une validation des clés AVANT écriture. keyed api_key,
# **BYOK** (byo user/org) : ce sont les données du client, il ne peut pas y avoir
# de clé oto partagée. 29 scopes granulaires côté éditeur, choisis à la CRÉATION
# de la clé — la sonde de connexion les lit et refuse une clé sans lecture CRM.
# ⚠️ Écrit ET envoie : `lightfield_emails(op="send")` part d'une boîte que le
# propriétaire de la clé a lui-même connectée, et est en dry-run PAR DÉFAUT.
CONNECTOR = _c(
    "lightfield", ["lightfield"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Lightfield",
    help="CRM agent-native : comptes, contacts, opportunités, notes, emails",
    href="https://lightfield.app",
)

CATEGORY = "CRM"
PUBLISHER = "Lightfield"
LOGO_DOMAIN = "lightfield.app"
