"""Déclaration de registre du connecteur `waalaxy`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# waalaxy : automatisation de prospection LinkedIn. API publique IMPORT-ONLY
# (4 endpoints : test, listes, campagnes actives, ajout de prospects à une
# liste ± campagne) — pas de lecture/suppression de prospects, pas d'inbox,
# pas de stats. keyed api_key (Bearer zpka_…, app → Settings → CRM Sync,
# plans Advanced/Business), byo-only : une clé = UN siège Waalaxy (= un
# compte LinkedIn), une clé plateforme n'aurait pas de sens.
CONNECTOR = _c(
    "waalaxy", ["waalaxy"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Waalaxy",
    help="prospection LinkedIn : pousser des prospects dans une liste et une "
         "campagne Waalaxy",
    href="https://app.waalaxy.com", credential_fields=(
        CredentialField("key", "Clé API (zpka_…)", secret=True,
                        help="Waalaxy → Settings → CRM Sync → Generate API key "
                             "(plan Advanced ou Business)"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Waalaxy"
LOGO_DOMAIN = "waalaxy.com"
