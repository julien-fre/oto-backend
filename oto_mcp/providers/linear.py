"""Déclaration de registre du connecteur `linear`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# linear : issues, projets, cycles (sprints), équipes, labels, commentaires,
# webhooks. keyed api_key (header `Authorization` SANS préfixe `Bearer` —
# spécificité Linear), **byo_user + byo_org**, pas de clé plateforme. Une clé
# API Linear est une clé PERSONNELLE (Settings → Security & access → Personal
# API keys) : elle agit au nom de son porteur, dans les workspaces où il a
# accès — comme notion ou slack. Elle se pose donc aussi bien pour soi que pour
# l'org (24/09/2026). Pas de pool plateforme : contrairement à un pool de
# crédits vendeur mutualisable (AI Ark, cf. le connecteur `linkedin` déposé,
# #279), rien ne justifie une clé partagée par oto.
CONNECTOR = _c(
    "linear", ["linear"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Linear",
    help="issues, projets, cycles, équipes, labels, commentaires, webhooks",
    href="https://linear.app",
)

CATEGORY = "Métier"
PUBLISHER = "Linear"
LOGO_DOMAIN = "linear.app"

DESCRIPTION = (
    "Le suivi de projet Linear : issues, projets, cycles (sprints), équipes, "
    "labels, commentaires et webhooks. La clé API Linear est personnelle : elle "
    "agit au nom de son porteur. Pose-la pour toi, ou pour l'org si elle doit "
    "servir à tous ; pas de clé partagée par oto."
)
