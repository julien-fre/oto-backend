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
    # ⚠️ Un seul namespace depuis le split du 2026-08-28 : les six canaux
    # (`linkedin_unipile`, `whatsapp`, `telegram`, `instagram`, `messenger`,
    # `twitter`) sont devenus des connecteurs à part entière — chacun sa carte, son
    # activation, son ACL, sa sélection — et un namespace n'appartient qu'à UN
    # connecteur. C'est la SEULE ligne de cette déclaration que le split touche :
    # tout le reste (clé, hosted-auth, flux multi-canal, label, modules) est le
    # code de production tel quel. Les canaux EMPRUNTENT la clé d'ici
    # (`credential_of="unipile"`, cf. `channel` en bas de ce fichier).
    "unipile", ["unipile"],
    auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", hosted_auth=True, personal_cross_org=True,
    # free-tier : clé plateforme OUVERTE à tous, gardée par l'OPTION couche-3 (has_option),
    # PAS par un allowlist de clé. Sans ce flag, un grant plateforme (onboarding d'un user
    # à unipile via le dashboard) fait passer la clé `open`→`closed`+share_down=[ce user]
    # et coupe TOUS les autres (panne all-users vécue 2× — org 194, puis un user). Avec le
    # flag, `platform_grant` ne pose QUE le quota, ne ferme jamais la clé (cf. oto-backend#245).
    platform_key_open=True,
    label="Messagerie hébergée (Unipile)",
    # Depuis le split du 2026-08-28, les six capacités promises ici SONT six autres
    # connecteurs, et celui-ci n'expose plus qu'un outil : `unipile_connect_start`.
    # L'aide continuait de promettre les six (corrigée le 2026-09-02).
    help="raccorder ton compte LinkedIn, WhatsApp, Telegram, Instagram, Messenger "
         "ou X — le préalable aux connecteurs de ces réseaux",
    href="https://www.unipile.com",
    modules=("unipile", "whatsapp", "telegram", "instagram", "messenger", "twitter"),
)

CATEGORY = "Prospection"
PUBLISHER = "Unipile"
LOGO_DOMAIN = "unipile.com"


# --- la FORME d'une connexion hébergée (le porteur de la clé la décrit) --------

def channel(name: str, *, hosted_channel: str, label: str, help: str,
            href: str, modules: tuple[str, ...] = ()):
    """Entrée de registre d'UN canal hébergé — six connecteurs, une seule forme.

    Chaque canal a son domicile (`providers/<nom>.py`) et y déclare ce qui le
    DISTINGUE : son nom (= son namespace de tools), son canal Unipile, son libellé,
    sa marque. Ce qu'il partage avec les cinq autres — les modes d'auth, la
    délégation de credential, le caractère par-personne — est décrit ICI, chez le
    porteur de la clé, parce que c'est une propriété du COMPTE et pas du canal.
    Recopier ces drapeaux six fois, c'est se donner cinq occasions de les faire
    diverger.

    Le canal ne DÉTIENT rien : `credential_of="unipile"` renvoie coffre, quota, clé
    plateforme et option couche-3 sur le compte. Ce qu'il possède en propre, c'est
    ce qui se gouverne par canal — activation, ACL, sélection, visibilité des tools,
    et sa connexion hébergée (un flux par carte, sans paramètre).

    ⚠️ `platform_key_open` n'est PAS recopié : il gouverne le PARTAGE d'une clé
    plateforme et se lit sur le porteur, après normalisation par la cascade. Le
    poser sur un canal serait de la configuration morte que le prochain lecteur
    croirait vivante — la forme exacte de la panne all-users de #245.
    `personal_cross_org`, LUI, est recopié : `call_axes` résout par NAMESPACE, et
    c'est ce drapeau qui fait exister l'axe `_account=` sur les tools du canal —
    donc les comptes accordés (#55). Les deux ne répondent pas à la même question.

    `href` est celui de la MARQUE du canal, jamais du fournisseur : ce que la
    personne connecte, c'est son LinkedIn ou son WhatsApp. Unipile est notre
    plomberie — elle reste nommée sur la carte `unipile`, qui EST le compte."""
    return _c(
        name, [name],
        auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
        secret_kind="api_key", hosted_auth=True, personal_cross_org=True,
        credential_of="unipile", hosted_channel=hosted_channel,
        label=label, help=help, href=href,
        modules=modules or (name,),
    )
