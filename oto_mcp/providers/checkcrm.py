"""Déclaration de registre du connecteur `checkcrm`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# checkcrm : wrappe l'API v1 de l'app "enrichment" d'un partenaire (job-change check
# LinkedIn + gestion des subsidiaries) — byo par défaut, chaque org configure sa
# propre clé enrichment (voir enrichment/docs/sf-api.md) ; clé plateforme
# GRANT-ONLY depuis le 26/08 (#405). Nommé "checkcrm" (un
# seul token, pas de underscore) : namespace_of prend le 1er token avant "_",
# "check_crm" romprait le préfixe des tools check_crm_* (résoudrait "check").
CONNECTOR = _c(
    "checkcrm", ["checkcrm"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    default_quota=0, platform_key_open=False,  # clé plateforme sur grant explicite (données achetées au crédit)
    secret_kind="api_key",
    # Ni un CRM, ni la vérification d'un CRM : de l'enrichissement. Le libellé
    # « Check CRM » et son aide en anglais (la seule du catalogue) ont tenu
    # jusqu'au 2026-09-02 ; le NOM, lui, ne bouge pas (les appelants s'y accrochent).
    label="Changement de poste & filiales",
    help="repérer les contacts qui ont changé d'employeur, et tenir la liste "
         "des filiales d'un groupe",
    href="https://enrichment-two.vercel.app",
)

CATEGORY = "Prospection"
# Produit d'un partenaire, pas d'Otomata — l'éditeur affiché disait « Otomata »
# (le défaut) jusqu'au 2026-09-02. La marque du partenaire n'est pas nommée ici :
# ce dépôt est public. Même raison pour l'absence de logo — il n'y a pas de
# domaine de marque publiable à donner au CDN.
PUBLISHER = "Partenaire Otomata"
SANS_LOGO_DE_MARQUE = True
