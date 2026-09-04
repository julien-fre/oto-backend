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
#
# ⚠️ L'ÉDITEUR EST LA PASSERELLE, PAS REDDIT (corrigé le 2026-09-02). La fiche
# a annoncé jusque-là « Reddit » comme éditeur, avec le logo reddit.com : on
# attribuait à Reddit un service que Reddit ne rend pas — l'appel part chez
# api.redditapis.com, un revendeur —, et la dépendance à cet intermédiaire
# n'apparaissait nulle part avant l'installation. Le NOM du connecteur et de
# ses tools ne bouge pas (des appelants s'y accrochent) : ce qui change est ce
# que la fiche DIT.
CONNECTOR = _c(
    "reddit", ["reddit"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=100, platform_key_open=True,
    label="Reddit",
    help="posts, subreddits & commentaires avec votes/métriques — via la "
         "passerelle tierce redditapis.com, pas l'API de Reddit",
    href="https://redditapis.com",
)

CATEGORY = "Web"
PUBLISHER = "redditapis.com (passerelle tierce)"
# Pas le logo de Reddit : le service rendu n'est pas le sien. Et redditapis.com
# n'est pas une marque que l'utilisateur reconnaîtrait — monogramme côté UI.
SANS_LOGO_DE_MARQUE = True

DESCRIPTION = (
    "Lire des posts, subreddits et commentaires Reddit avec leurs métriques "
    "(score, nombre de commentaires, ratio d'upvotes), via une passerelle "
    "tierce (redditapis.com) — l'API officielle de Reddit est fermée au self- "
    "serve. Clé plateforme partagée avec quota quotidien, ou clé de ta propre "
    "passerelle."
)
