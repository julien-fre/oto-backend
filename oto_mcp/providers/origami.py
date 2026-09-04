"""Déclaration de registre du connecteur `origami`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# origami : campagnes email + LinkedIn (origami.chat, API v2 Bearer `og_live_…`) —
# tables de leads (upload CSV, upsert), campagnes rédigées par l'agent Origami,
# lancement, pause/reprise, stats, séquences. keyed api_key, **BYOK** (byo
# user/org) : les crédits et les envois sont ceux du compte de l'org.
# ⚠️ Premier montage tiers dont l'ÉCRITURE ENVOIE hors plateforme (lancer =
# emails + messages LinkedIn à des personnes réelles) : chaque tool mutant est
# gaté `dry_run` (convention oto-wide), le lancement est dry_run=True par
# défaut. Décision d'acceptabilité laissée au mainteneur (cf. tools/origami.py).
CONNECTOR = _c(
    "origami", ["origami"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Origami",
    help="campagnes email + LinkedIn : tables, campagnes, lancement, statistiques",
    href="https://origami.chat",
)

CATEGORY = "Prospection"
PUBLISHER = "Origami"
LOGO_DOMAIN = "origami.chat"

DESCRIPTION = (
    "Des campagnes email ET LinkedIn pilotées par l'agent Origami : tables de "
    "leads (import CSV), rédaction et lancement de campagne, pause et reprise, "
    "statistiques. Chaque envoi réel passe par un mode d'essai (dry-run) activé "
    "par défaut, avant d'atteindre de vraies personnes."
)
