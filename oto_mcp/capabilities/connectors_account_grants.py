"""Autorisation de compte connecteur partagé (otomata-private#55) — surface du
PROPRIÉTAIRE : accorder / révoquer à un user nommé le droit d'opérer SON compte
Unipile sur un canal (agence multi-clients, compte d'org opéré par une équipe,
freelance externe). **Cross-org assumé** : le grantee n'a PAS besoin de partager
une org avec le propriétaire — on partage son PROPRE compte, à qui on veut.

Deny-by-default, révocation à effet immédiat (le grant est revalidé à chaque appel
dans la résolution, cf. `connector_identities.resolve_operated_account_id`), audité
(`granted_by`/`granted_at`). Autz `SUB_ONLY` : « réservé au propriétaire » est
garanti PAR CONSTRUCTION — `owner_sub := ctx.sub`, jamais accepté d'un param client
(même verrou structurel que l'injection `org_id` des combinateurs). Aucune escalade
org_admin : seul le propriétaire du compte accorde (exigence #55).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import db
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

Channel = Literal["linkedin", "whatsapp", "telegram", "instagram", "messenger", "twitter"]


def _provider_for(channel: str) -> str:
    """Canal front → provider DB (source unique : `tools/unipile.UNIPILE_CHANNELS`).
    Import paresseux — pas de dépendance module-level capacités → runtime tools."""
    from ..tools.unipile import UNIPILE_CHANNELS
    return UNIPILE_CHANNELS[channel]


def _resolve_grantee(ctx: ResolvedCtx, grantee: str) -> dict:
    """`grantee` = sub OU email → fiche user. Le propriétaire partage SON PROPRE
    compte (owner := ctx.sub par construction) → il peut l'accorder à N'IMPORTE
    QUEL user oto, **y compris hors de ses orgs** (cross-org assumé : agence /
    freelance externe). Seuls garde-fous : l'user doit exister, et pas de
    self-grant (tu opères déjà ton compte)."""
    if "@" in grantee:
        user = db.get_user_by_email(grantee)
    else:
        user = db.get_user(grantee)
    if not user:
        raise AuthzDenied(404, "unknown_user", f"Utilisateur inconnu : {grantee}")
    if user["sub"] == ctx.sub:
        raise AuthzDenied(400, "self_grant", "Tu opères déjà ton propre compte.")
    return user


class AccountGrantsListInput(BaseModel):
    pass


class AccountGrantInput(BaseModel):
    channel: Channel
    grantee: str                         # sub OU email du membre autorisé


class GrantedByMe(BaseModel):
    """Une autorisation que J'AI accordée : « untel peut opérer mon compte sur ce
    canal »."""
    # ⚠️ `provider` n'est PAS le `channel` de l'entrée : c'est le provider DB, en
    # MAJUSCULES (`LINKEDIN`, `WHATSAPP`…). On accorde par `channel=linkedin` et on
    # relit `provider="LINKEDIN"` — un client qui compare les deux tel quel ne
    # matche jamais.
    provider: str
    # État LIVE du compte (LEFT JOIN), pas le snapshot d'audit du grant : `null`
    # si le canal a été déconnecté depuis — le grant existe encore mais est INERTE.
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    grantee_sub: str
    grantee_email: Optional[str] = None     # null si l'user n'a pas de ligne `users`
    grantee_name: Optional[str] = None
    granted_by: Optional[str] = None
    granted_at: Optional[str] = None
    # DÉRIVÉ de `account_id IS NOT NULL` : `false` = j'ai déconnecté le canal, le
    # grant dort. Ce n'est ni une révocation ni une erreur — reconnecter le
    # ressuscite tel quel.
    active: bool


class GrantedToMe(BaseModel):
    """Une autorisation que J'AI REÇUE : un compte d'autrui que je peux opérer."""
    provider: str                           # provider DB en MAJUSCULES (cf. GrantedByMe)
    owner_sub: str
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    account_id: Optional[str] = None        # null = le propriétaire a déconnecté le canal
    account_name: Optional[str] = None
    # L'org sous laquelle le PROPRIÉTAIRE a connecté ce compte — dit d'OÙ vient le
    # partage. Le grant lui-même n'est scopé à aucune org (cross-org assumé) : ce
    # n'est donc pas un filtre d'accès.
    owner_org_id: Optional[int] = None
    owner_org_name: Optional[str] = None
    granted_at: Optional[str] = None
    active: bool


class AccountGrants(BaseModel):
    """Les deux faces du partage de compte connecteur (#55), du point de vue du
    caller. Deny-by-default : deux listes vides = personne n'opère rien."""
    granted_by_me: list[GrantedByMe]
    granted_to_me: list[GrantedToMe]


class AccountGrantCreated(BaseModel):
    """Écho d'une autorisation accordée."""
    ok: bool
    channel: str                            # le canal FRONT tel que passé (minuscules)
    account_id: str                         # le compte visé, snapshot au moment du grant
    grantee_sub: str                        # sub RÉSOLU (l'entrée pouvait être un email)
    grantee_email: Optional[str] = None
    # Limitation documentée, renvoyée telle quelle : le grant autorise, il ne
    # fournit pas la clé. Le bénéficiaire doit encore joindre ce compte avec SA
    # clé (partagée org/plateforme = OK ; une clé BYO perso ne le voit pas → 404
    # à l'appel).
    note: str


class AccountGrantRevoked(BaseModel):
    """Écho d'une révocation. Idempotent : `revoked=false` = il n'y avait pas de
    grant à retirer, pas un refus."""
    ok: bool
    channel: str
    # ⚠️ Écho de l'entrée quand elle n'a pas pu être résolue : un email INCONNU
    # est renvoyé tel quel ici (aucune erreur — le retrait ne fait que ne rien
    # trouver, là où `grant` aurait levé un 404). Un `grantee_sub` contenant un
    # « @ » + `revoked:false` est donc le signe d'une cible mal nommée, pas d'un
    # grant déjà retiré.
    grantee_sub: str
    revoked: bool


def _list(ctx: ResolvedCtx, inp: AccountGrantsListInput) -> dict:
    return {
        "granted_by_me": db.list_account_grants_by_owner(ctx.sub),
        "granted_to_me": db.list_account_grants_to(ctx.sub),
    }


def _grant(ctx: ResolvedCtx, inp: AccountGrantInput) -> dict:
    provider = _provider_for(inp.channel)
    user = _resolve_grantee(ctx, inp.grantee)
    # Scope membre (ADR 0033) : le compte du propriétaire vit dans SON org de
    # contexte — `ctx.org_id` est injecté par SUB_ONLY (= access.current_org).
    account_id = db.get_unipile_account_id(ctx.sub, ctx.org_id, provider)
    if not account_id:
        raise AuthzDenied(404, "channel_not_connected",
                          f"Tu n'as pas de compte {inp.channel} connecté — connecte-le "
                          "d'abord (dashboard, carte du connecteur).")
    db.set_account_grant(ctx.sub, provider, account_id, user["sub"], granted_by=ctx.sub)
    return {
        "ok": True, "channel": inp.channel, "account_id": account_id,
        "grantee_sub": user["sub"], "grantee_email": user.get("email"),
        # Limitation documentée : la clé du grantee doit joindre ce compte (clé
        # partagée org/plateforme = OK ; owner sur une clé BYO perso ≠ 404 à l'appel).
        "note": "Le membre autorisé opère ce compte via le sélecteur d'identité "
                "(oto_identity op=set) ou un pin de projet.",
    }


def _revoke(ctx: ResolvedCtx, inp: AccountGrantInput) -> dict:
    provider = _provider_for(inp.channel)
    if "@" in inp.grantee:
        user = db.get_user_by_email(inp.grantee)
        grantee_sub = user["sub"] if user else inp.grantee
    else:
        grantee_sub = inp.grantee
    revoked = db.clear_account_grant(ctx.sub, provider, grantee_sub)
    # Hygiène : efface le pointeur du grantee s'il opérait ce compte. Le backstop
    # ne repose PAS dessus (grant re-checké à chaque appel).
    db.clear_operated_pointers_to(ctx.sub, provider, grantee_sub)
    return {"ok": True, "channel": inp.channel, "grantee_sub": grantee_sub,
            "revoked": revoked}


CAPABILITIES += [
    Capability(
        key="connectors.account_grants.list", handler=_list, Input=AccountGrantsListInput,
        authz=SUB_ONLY, Output=AccountGrants,
        description="List the connector account authorizations you granted (who may operate "
                    "your Unipile accounts, per channel) and those granted to you (accounts "
                    "you may operate). Deny-by-default: no grant = nobody but the owner.",
        rest=RestBinding("GET", "/api/me/connector-accounts/grants"),
    ),
    Capability(
        key="connectors.account_grants.grant", handler=_grant, Input=AccountGrantInput,
        authz=SUB_ONLY, Output=AccountGrantCreated,
        description="[account owner] Authorize any oto user (grantee = email or sub — including "
                    "someone OUTSIDE your orgs, e.g. an external freelancer or agency) to OPERATE "
                    "your connected account on a channel (linkedin, whatsapp, …), acting as you. "
                    "Only the owner can grant; revocable anytime with immediate effect; audited.",
        rest=RestBinding("POST", "/api/me/connector-accounts/{channel}/grants"),
    ),
    Capability(
        key="connectors.account_grants.revoke", handler=_revoke, Input=AccountGrantInput,
        authz=SUB_ONLY, Output=AccountGrantRevoked,
        description="[account owner] Revoke a member's authorization to operate your account "
                    "on a channel. Immediate: their next call under your identity fails "
                    "explicitly. Idempotent.",
        rest=RestBinding("DELETE", "/api/me/connector-accounts/{channel}/grants"),
    ),
]
