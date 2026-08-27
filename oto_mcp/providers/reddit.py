"""Déclaration de registre du connecteur `reddit`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# reddit : lecture posts/subreddits/commentaires AVEC métriques (score,
# num_comments, upvote_ratio, pagination, arbre imbriqué) via la passerelle
# REST redditapis.com. L'API Reddit officielle est fermée en self-serve
# (Responsible Builder Policy fin 2025) et le JSON anonyme est bloqué (403
# IP datacenter) → l'ancien connecteur RSS (sans métriques) est remplacé.
# Clé plateforme partagée (Otomata paie l'usage) + quota/jour pour borner le
# coût ; BYO possible (l'org pose sa propre clé redditapis).
CONNECTOR = _c(
    "reddit", ["reddit"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=100, platform_key_open=True,
    label="Reddit", help="posts, subreddits & commentaires avec votes/métriques",
    href="https://redditapis.com",
)

CATEGORY = "Web"
PUBLISHER = "Reddit"
LOGO_DOMAIN = "reddit.com"
