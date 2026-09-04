"""Déclaration de registre du connecteur `linkedin_unipile` — la session LinkedIn opérée.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE. La forme
commune aux six connexions hébergées vit chez le porteur de la clé
(`providers/unipile.channel`) — ici, ce qui distingue CELLE-CI.
"""
from __future__ import annotations

from .unipile import channel

# linkedin_unipile : TA session LinkedIn — recherche, profils, posts, réseau, offres
# d'emploi, messagerie. 8 tools à `op=` sous le namespace `linkedin_unipile`.
#
# ⚠️ Le nom du connecteur EST son namespace, et il GARDE le suffixe du fournisseur
# (ADR 0010 §Amendement 2026-08-10) : le namespace porte la CAPACITÉ (LinkedIn)
# suffixée du FOURNISSEUR quand plusieurs fournisseurs non substituables la rendent —
# ici Unipile (la session OPÉRÉE) et AI Ark (`linkedin_aiark_*`, de la donnée
# ACHETÉE au crédit : email, mobile, reverse-lookup, rien de tout ça n'existe sur
# LinkedIn). `namespace_of` résout au plus long préfixe DÉCLARÉ : les deux gardent
# un gate distinct. Verrouillé par tests/test_linkedin.py.
#
# Les noms de tools sont un CONTRAT consommé hors dépôt (procédures en base de
# plusieurs orgs, guides plateforme, un métrage d'usage) : le split du
# 2026-08-28 ne les touche pas. Ce qui change, c'est la CARTE — elle s'appelle
# « LinkedIn », renvoie à linkedin.com et ne nomme pas le fournisseur : ce que la
# personne connecte, c'est son compte LinkedIn. Unipile reste nommé sur la carte
# `unipile`, qui EST le compte fournisseur et porte la clé.
#
# Ses tools vivent dans `tools/unipile.py` (avec la factory de messagerie partagée
# et `unipile_connect_start`) — d'où le `modules=("unipile",)` explicite : le module
# ne porte pas le nom du connecteur, et `register_all` dédoublonne les modules.
CONNECTOR = channel(
    "linkedin_unipile",
    hosted_channel="LINKEDIN",
    label="LinkedIn",
    help="Ta session LinkedIn — recherche, profils, posts, réseau, jobs, messagerie. "
         "Ton compte se connecte chez Unipile, notre prestataire, qui détient la session.",
    href="https://www.linkedin.com",
    modules=("unipile",),
)

CATEGORY = "Prospection"
LOGO_DOMAIN = "linkedin.com"
DESCRIPTION = (
    "Ta session LinkedIn, opérée pour toi : recherche de personnes et "
    "d'entreprises, profils, posts et commentaires, réseau (invitations, "
    "relations), offres d'emploi et messagerie. Tu connectes TON compte et tu agis "
    "comme toi-même. Pour un email ou un mobile qu'un profil ne publie pas, c'est "
    "un connecteur d'enrichissement qu'il faut (AI Ark, Dropcontact, FullEnrich…)."
)
