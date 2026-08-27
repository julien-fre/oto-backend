"""Déclaration de registre du connecteur `unipile`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# unipile : LinkedIn hébergé (recherche/scrape/messagerie) via l'API Unipile.
# La session LinkedIn vit chez Unipile (vrai Chrome + proxy résidentiel) →
# contourne empreinte TLS + isolation de session du browser local (#5). Keyed
# api_key (résolu via resolve_api_key, cascade user > org). byo_user (BYO) OU
# byo_org (l'org pose l'abonnement Otomata, ses membres connectent leur LinkedIn
# par hosted-auth). Hors socle (comme tout le catalogue, 16/07) ; l'option payante
# (couche 3) gate l'usage plateforme, le BYO reste libre. Le **dsn** (API v2 :
# gateway `api.unipile.com`) est résolu côté client (env `UNIPILE_DSN`, défaut
# api.unipile.com = celui d'Otomata) — PAS un champ de credential tant qu'un BYO
# sur un autre endpoint n'existe pas (déféré ; single-field = compatible avec le
# stockage org-secret existant, mono-valeur).
# ⚠️ Namespace des tools LinkedIn = `linkedin_unipile` (multi-token), pas `unipile` :
# ADR 0010 §Amendement 2026-08-10 — le namespace porte la CAPACITÉ, suffixée du
# FOURNISSEUR quand plusieurs fournisseurs non substituables la rendent (ici Unipile,
# session opérée · AI Ark, donnée achetée). `namespace_of` résout au plus long préfixe
# DÉCLARÉ ici : les deux gardent donc un gate distinct. `unipile` reste déclaré pour
# `unipile_connect_start` (multi-canal : linkedin|whatsapp|… — il n'appartient à aucune
# capacité, sa place cible est `oto_connector op=connect`, cf. oto-backend#279).
CONNECTOR = _c(
    "unipile", ["linkedin_unipile", "unipile", "whatsapp", "telegram",
                "instagram", "messenger", "twitter"],
    auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", hosted_auth=True, personal_cross_org=True,
    # free-tier : clé plateforme OUVERTE à tous, gardée par l'OPTION couche-3 (has_option),
    # PAS par un allowlist de clé. Sans ce flag, un grant plateforme (onboarding d'un user
    # à unipile via le dashboard) fait passer la clé `open`→`closed`+share_down=[ce user]
    # et coupe TOUS les autres (panne all-users vécue 2× — org 194, puis un user). Avec le
    # flag, `platform_grant` ne pose QUE le quota, ne ferme jamais la clé (cf. oto-backend#245).
    platform_key_open=True,
    label="Messagerie hébergée (Unipile)",
    help="LinkedIn + WhatsApp + Telegram + Instagram + Messenger + X/Twitter hébergés (recherche/scrape/messagerie)",
    href="https://www.unipile.com",
    modules=("unipile", "whatsapp", "telegram", "instagram", "messenger", "twitter"),
)

CATEGORY = "Prospection"
PUBLISHER = "Unipile"
LOGO_DOMAIN = "unipile.com"
