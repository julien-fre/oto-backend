"""Capacités d'invitation d'org (onboarding SaaS, ADR 0009).

Émission : une invitation a TOUJOURS un code court partageable (lien
`/invitation/<code>`) ET, si on le demande, part par mail. L'émetteur choisit
`send_email` ; sans envoi, il partage le code lui-même.

create/list/revoke gatés `ORG_ADMIN_OF` (platform-admin par escalade) ; accept en
`SUB_ONLY` (modèle bearer : le code/token suffit, cf. `org_store.accept_*`).

Deux gestes NÉGATIFS, à ne pas confondre — c'est la distinction que #654 a fait
naître : la **révocation** (`ORG_ADMIN_OF`) est l'émetteur qui retire ce qu'il a
émis, le **refus** (`SUB_ONLY` + adresse confrontée) est l'invité qui dit non.
Jusqu'au 2026-09-01 seul le premier existait : une personne qui ne voulait pas
rejoindre gardait dans son inbox un badge qu'elle ne pouvait pas éteindre.
"""
from __future__ import annotations

import os

from typing import Optional

from pydantic import BaseModel

from ... import db, org_store
from ...auth import facade as oauth_facade
from ... import email as email_mod  # alias : le param `email` d'emit_invitation masquerait le module
from .._authz import ORG_ADMIN_OF, SUB_ONLY
from .._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}
_INVITE_TTL_DAYS = int(os.environ.get("OTO_MCP_INVITE_TTL_DAYS", "7"))


def _invite_base(front_base: str | None = None) -> str:
    """Base PUBLIQUE des liens d'invitation partagés (court, marketing).
    `front_base` = le front qui héberge l'org (`orgs.front_base_url`, ex. un tenant tiers) ;
    absent = oto, où `oto.cx/invitation/...` redirige vers le dashboard (règle Caddy)."""
    return (front_base or os.environ.get("OTO_INVITE_BASE_URL", "https://oto.cx")).rstrip("/")


def _nominal_url(code: str, email_addr: str | None = None, *,
                 front_base: str | None = None) -> str:
    """Lien d'une invitation nominative : `/invitation/<code>`. Augmenté d'un
    magic-link Logto (OTT) quand on connaît l'email invité → connexion sans saisie
    de code. Sans email = lien nu, partageable à la main.

    ⚠️ L'OTT est minté sur NOTRE Logto (`LOGTO_ENDPOINT`, un seul global) et
    n'authentifie que contre lui. Une org sous front tiers a son propre émetteur
    (ex. `auth.<tenant>.ai` depuis le 2026-08-03) — un OTT oto y serait inerte,
    soit un échec de connexion silencieux pour l'invité. Donc pas de magic-link dès
    que l'org porte un `front_base_url` : le code nu suffit (modèle bearer).
    Le jour de l'étage tenant (ADR 0052), la condition devient « l'émetteur du tenant
    est le nôtre » — dont la présence d'un front tiers n'est ici qu'un proxy."""
    url = f"{_invite_base(front_base)}/invitation/{code}"
    if email_addr and not front_base:
        return oauth_facade.magic_url(url, email_addr.strip())
    return url


def _same_address(a: str | None, b: str | None) -> bool:
    """Les deux adresses désignent-elles la même boîte (strip + casse) ?

    Comparaison SEULE, jamais de validation : on décide ici d'un droit à partir de
    deux valeurs déjà stockées (l'email de l'invitation, celui du compte), pas de la
    forme d'une saisie — lever un `invalid_email` sur une donnée en base rendrait un
    400 incompréhensible à qui ne fait que cliquer « refuser ». Une adresse absente
    n'égale RIEN, pas même une autre absence : une invitation anonyme n'est adressée
    à personne, donc à personne en particulier."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a) and a == b


def _norm_email(raw: str | None, *, required: bool) -> str | None:
    e = (raw or "").strip().lower() or None
    if required and not e:
        raise AuthzDenied(400, "invalid_email", "Email requis pour un envoi par mail.")
    if e is not None and "@" not in e:
        raise AuthzDenied(400, "invalid_email", "Email invalide.")
    return e


# --- Sorties ----------------------------------------------------------------

class InvitationEmitted(BaseModel):
    """Invitation créée. Forme commune aux TROIS niveaux de la cascade
    (plateforme / org / équipe) — d'où le champ `role` unique, qui porte le rôle
    d'équipe quand il y en a un, sinon le rôle d'org.

    ⚠️ **`emailed: false` confond deux causes** : « tu n'as pas demandé d'envoi »
    (`send_email=false`, cas nominal du partage manuel) et « l'envoi a échoué ». Rien
    dans la réponse ne les distingue — un front qui affiche « mail envoyé » sur
    `ok: true` peut mentir. Traiter `emailed: false` comme « à partager soi-même ».

    ⚠️ `invite_url` est le lien **NU** (`/invitation/<code>`). Le magic-link Logto,
    quand il est minté, ne part que dans le MAIL — le partager depuis cette réponse
    ne transporte donc jamais la connexion sans saisie. Et une org rattachée à un
    front tiers n'en obtient aucun (l'OTT serait inerte sur son émetteur).

    ⚠️ `code` est un **secret porteur** : le détenir suffit à rejoindre l'org (il n'y
    a pas de vérification que l'accepteur est bien `email`). À traiter comme un jeton,
    pas comme un identifiant.

    Une adresse déjà membre, ou déjà invitée (invitation encore valide), est refusée
    en 409 — `already_member` / `already_invited`, l'invitation existante dans
    `details` (depuis le 29/08/2026, #622 ; avant, un 200 et un code de plus). Une
    invitation expirée, consommée ou révoquée ne bloque pas."""
    ok: bool
    # Email NORMALISÉ (strip + minuscules), ou None quand l'invitation est un simple
    # code à partager.
    email: Optional[str] = None
    role: str
    code: str
    invite_url: str
    emailed: bool


class InvitationEntry(BaseModel):
    """Une invitation en attente. Elle porte `code`, donc le secret porteur : cette
    liste est du matériel sensible, pas un simple journal."""
    id: int
    email: Optional[str] = None
    code: str
    org_role: Optional[str] = None
    group_role: Optional[str] = None
    org_id: Optional[int] = None
    group_id: Optional[int] = None
    invited_by: Optional[str] = None
    source: Optional[str] = None
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    org_name: Optional[str] = None
    group_name: Optional[str] = None
    # Dérivé (team si group_id, sinon org si org_id, sinon platform). Sur CETTE
    # capacité il vaut toujours "org" — les invitations d'équipe en sont exclues.
    scope: str


class Invitations(BaseModel):
    """Invitations d'ORG en attente. ⚠️ **Une liste vide ne veut pas dire « personne
    n'a été invité »** : trois populations en sont absentes — les invitations
    d'ÉQUIPE (`group_id` non nul, servies par l'écran équipe), les **acceptées** et
    les **expirées**. Cette vue est une file d'attente, pas un historique."""
    invitations: list[InvitationEntry]


class InvitationRevoked(BaseModel):
    """Révocation. Non idempotente : une invitation inconnue **ou déjà acceptée** rend
    un 404 (`unknown_invitation`) — accepter puis révoquer est impossible, il faut
    retirer le membre. `revoked` réécho l'id demandé."""
    ok: bool
    revoked: int


class InvitationDeclined(BaseModel):
    """Invitation REFUSÉE (oto-backend#654). L'invitation est fermée : elle quitte
    l'inbox de l'invité, la file de l'émetteur et le compte `counters.home`, et plus
    aucun chemin ne peut la consommer (ni `accept`, ni la reprise automatique au
    signup par l'email).

    ⚠️ **Refuser n'est pas quitter.** Aucune appartenance n'est créée ni retirée : si
    la personne était déjà membre de l'org par ailleurs, elle l'est toujours. Pour
    partir, c'est `DELETE /api/me/orgs/{id}/membership`.

    ⚠️ **`org_id: null` avec `ok: true`** = invitation de PLATEFORME refusée (il n'y
    avait pas d'org à rejoindre) — même piège que sur l'acceptation.

    Le refus n'est **pas définitif pour la personne, seulement pour CETTE
    invitation** : l'émetteur peut en émettre une nouvelle immédiatement (une
    invitation refusée ne déclenche plus le 409 `already_invited` de #622). Il n'est
    en revanche **pas notifié** — la plateforme n'a pas encore de préférences de
    notification ; il voit seulement l'invitation quitter sa file, comme s'il l'avait
    révoquée.

    Re-refuser la même invitation est idempotent (même réponse)."""
    ok: bool
    declined: bool
    scope: str                                  # platform | org | team
    org_id: Optional[int] = None
    group_id: Optional[int] = None
    name: Optional[str] = None                  # nom de l'org qu'on ne rejoindra pas


class InvitationAccepted(BaseModel):
    """Invitation consommée (modèle bearer : le code ou le token SUFFIT, l'identité de
    l'accepteur n'est pas confrontée à l'email invité).

    ⚠️ **`org_id: null` avec `ok: true` est un succès qui ne rejoint rien** : c'est une
    invitation de PLATEFORME (attribution d'onboarding), consommée sans adhésion. Un
    front qui redirige vers `/orgs/{org_id}` construira une URL invalide.

    Quand `org_id` est présent, l'org rejointe devient bien l'org MAISON — `active_org`
    en est l'écho fidèle (`set_active_org` est appelé explicitement), pas une supposition.

    Ré-accepter par le MÊME sub est idempotent (même réponse) ; par un autre, c'est un
    410 `invalid_or_expired`."""
    ok: bool
    org_id: Optional[int] = None
    org_role: Optional[str] = None
    group_id: Optional[int] = None
    group_role: Optional[str] = None
    active_org: Optional[int] = None
    name: Optional[str] = None


# --- Inputs -----------------------------------------------------------------

class InviteCreateInput(BaseModel):
    org_id: int
    email: str | None = None
    role: str = "org_member"
    send_email: bool = True


class InviteListInput(BaseModel):
    org_id: int


class InviteRevokeInput(BaseModel):
    org_id: int
    invite_id: int


class InviteAcceptInput(BaseModel):
    token: str | None = None
    code: str | None = None


class InviteRejectInput(BaseModel):
    """Mêmes deux façons de désigner l'invitation que l'acceptation — c'est la
    symétrie que le front tiers demandait (#654). Rien de plus : le refus ne
    prend pas d'org_id, l'invitation porte déjà sa cible."""
    token: str | None = None
    code: str | None = None


# --- Émission partagée (cascade plateforme/org/équipe) ----------------------

def emit_invitation(ctx: ResolvedCtx, *, org_id: int | None, email: str | None,
                    send_email: bool, source: str, role: str,
                    target_name: str | None,
                    group_id: int | None = None,
                    group_role: str | None = None) -> dict:
    """Cœur partagé d'émission d'une invitation, commun aux 3 niveaux de la cascade
    (plateforme/org/équipe). Crée la ligne (scope dérivé des cibles), forge le lien
    `/invitation/<code>` et, si demandé, envoie le mail (`target_name` = ce qu'on
    rejoint, None = plateforme → « rejoindre oto »).

    Le front destinataire (base du lien, marque du mail) est **dérivé de l'org cible**
    (`orgs.front_*`), jamais déclaré par l'appelant : une invitation ne peut pas
    prétendre venir d'un front auquel l'org n'appartient pas, et les 3 niveaux en
    héritent sans rien porter. Sans org (invitation plateforme pure) = oto."""
    email_addr = _norm_email(email, required=send_email)
    front_base, brand = org_store.org_front(org_id)
    _, _token, code = org_store.create_invitation(
        org_id, email_addr, role, invited_by=ctx.sub, ttl_days=_INVITE_TTL_DAYS,
        source=source, group_id=group_id, group_role=group_role)
    share_url = _nominal_url(code, front_base=front_base)
    emailed = False
    if send_email and email_addr:
        inviter = (db.get_user(ctx.sub) or {}).get("email")
        # Locale du DESTINATAIRE, si connue (oto-backend#700) : un invité qui a
        # déjà un compte oto (ré-invitation, autre org) a peut-être déjà posé sa
        # préférence via `me.locale.set`. Une adresse jamais vue n'a pas encore
        # de ligne `users` ⟹ locale=None ⟹ le gabarit sert FR (comportement
        # d'avant) — la détection de langue pour un contact jamais loggé reste
        # hors scope de ce lot.
        locale = (db.get_user_by_email(email_addr) or {}).get("locale")
        emailed = email_mod.send_invite_email(
            email_addr, target_name, _nominal_url(code, email_addr, front_base=front_base),
            inviter, brand=brand or "oto", locale=locale)
    return {"ok": True, "email": email_addr, "role": group_role or role, "code": code,
            "invite_url": share_url, "emailed": emailed}


# --- Handlers ---------------------------------------------------------------

def _refuse_member_or_invited(org_id: int, email_addr: str | None) -> None:
    """Le refus #622 (29/08/2026), sur l'adresse NORMALISÉE : inviter un membre actuel
    n'a pas de sens, et une deuxième invitation vivante pour la même adresse est un
    deuxième secret porteur. Sans adresse (code à partager), rien à comparer.
    Membre d'abord : il n'y a rien à renvoyer, la personne est déjà là. Invitée
    ensuite, avec de quoi RENVOYER l'existante — jamais son code, qui suffit à
    rejoindre l'org. Expirée, consommée ou révoquée = plus dans la file = pas un
    doublon."""
    if not email_addr:
        return
    if org_store.get_org_member_by_email(org_id, email_addr):
        raise AuthzDenied(409, "already_member",
                          f"{email_addr} est déjà membre de cette org : rien à inviter.")
    inv = org_store.find_pending_invitation(org_id, email_addr)
    if inv:
        raise AuthzDenied(
            409, "already_invited",
            f"{email_addr} a déjà une invitation en attente (#{inv['id']}, expire le "
            f"{inv['expires_at']}) : la renvoyer ou la révoquer plutôt qu'en émettre "
            "une deuxième.",
            details={"invitation": {"id": inv["id"], "created_at": inv["created_at"],
                                    "expires_at": inv["expires_at"]}})


def _invite_create(ctx: ResolvedCtx, inp: InviteCreateInput) -> dict:
    if inp.role not in org_store.ORG_ROLES:
        raise AuthzDenied(400, "invalid_role", f"Rôle invalide : {inp.role!r}.")
    org = org_store.get_org(inp.org_id)
    if not org:
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    _refuse_member_or_invited(inp.org_id, _norm_email(inp.email, required=inp.send_email))
    return emit_invitation(ctx, org_id=inp.org_id, email=inp.email,
                           send_email=inp.send_email, source="org_admin",
                           role=inp.role, target_name=org["name"])


def _invite_list(ctx: ResolvedCtx, inp: InviteListInput) -> dict:
    return {"invitations": org_store.list_invitations(inp.org_id)}


def _invite_revoke(ctx: ResolvedCtx, inp: InviteRevokeInput) -> dict:
    if not org_store.revoke_invitation(inp.org_id, inp.invite_id):
        raise AuthzDenied(404, "unknown_invitation", "Invitation introuvable ou déjà acceptée.")
    return {"ok": True, "revoked": inp.invite_id}


def _invite_accept(ctx: ResolvedCtx, inp: InviteAcceptInput) -> dict:
    """Accepte une invitation d'org par token mail (legacy) ou code court nominatif.
    Modèle bearer : le secret suffit."""
    if inp.token:
        res = org_store.accept_invitation(inp.token, ctx.sub)
    elif inp.code:
        res = org_store.accept_invitation_by_code(inp.code, ctx.sub)
    else:
        raise AuthzDenied(400, "missing_token", "Aucun token ni code d'invitation fourni.")
    if not res:
        raise AuthzDenied(410, "invalid_or_expired", "Invitation invalide, expirée ou déjà utilisée.")
    org = org_store.get_org(res["org_id"]) if res.get("org_id") else None
    return {"ok": True, "org_id": res.get("org_id"), "org_role": res.get("org_role"),
            "group_id": res.get("group_id"), "group_role": res.get("group_role"),
            "active_org": res.get("org_id"), "name": org["name"] if org else None}


def _invite_reject(ctx: ResolvedCtx, inp: InviteRejectInput) -> dict:
    """Refuse une invitation : elle est fermée sans que personne ne rejoigne rien.

    **Le refus n'est PAS bearer, contrairement à l'acceptation, et c'est délibéré.**
    Accepter avec un secret qu'on détient est un geste sur soi ; refuser avec le même
    secret DÉTRUIT l'invitation d'un tiers — un code partagé par erreur deviendrait
    une porte pour annuler l'onboarding de quelqu'un d'autre, sans appartenance créée
    donc sans trace visible. On exige donc que l'invitation soit ADRESSÉE à l'adresse
    du compte appelant. Ce n'est pas un rétrécissement du besoin : le badge que cette
    issue existe pour éteindre est lui-même indexé par l'email (`me.inbox` passe par
    `list_pending_invitations_for_email`), donc quiconque le voit passe cette garde.

    Corollaire assumé : une invitation ANONYME (émise sans email, code à partager
    soi-même) ne se refuse pas — elle n'est adressée à personne, elle n'allume aucun
    badge, et la retirer est le geste de son émetteur (révocation).

    Les gardes sont dans l'ORDRE où elles s'appliquent — c'est un contrat, le premier
    refus qui mord est celui qui est rendu."""
    if not inp.token and not inp.code:
        raise AuthzDenied(400, "missing_token", "Aucun token ni code d'invitation fourni.")
    inv = org_store.peek_invitation(token=inp.token, code=inp.code)
    # Inconnue, expirée, déjà acceptée, ou refusée par QUELQU'UN D'AUTRE : même
    # réponse que l'acceptation dans les mêmes cas — il n'y a plus rien à refuser.
    if (inv is None or not inv.get("live") or inv.get("accepted_at") is not None
            or (inv.get("declined_at") is not None and inv.get("declined_sub") != ctx.sub)):
        raise AuthzDenied(410, "invalid_or_expired",
                          "Invitation invalide, expirée ou déjà traitée.")
    if not _same_address(inv.get("email"), (db.get_user(ctx.sub) or {}).get("email")):
        raise AuthzDenied(
            403, "not_the_invitee",
            "Cette invitation ne t'est pas adressée : seule la personne invitée peut "
            "la refuser. L'émetteur, lui, peut la révoquer.")
    # Déjà refusée par moi : idempotent, on ne réécrit pas la date du refus.
    if inv.get("declined_at") is None:
        org_store.mark_invitation_declined(inv["id"], ctx.sub)
    return {"ok": True, "declined": True, "scope": inv["scope"],
            "org_id": inv.get("org_id"), "group_id": inv.get("group_id"),
            "name": inv.get("org_name")}


CAPABILITIES += [
    Capability(
        key="org.invite.create", handler=_invite_create, Input=InviteCreateInput,
        authz=ORG_ADMIN_OF("org_id"), Output=InvitationEmitted,
        description="Invite someone to an org you administer (role: org_member|org_admin). "
                    "send_email=true mails a link; false returns a short code to share yourself.",
        rest=(RestBinding("POST", "/api/orgs/{id}/invitations", _ID),
              RestBinding("POST", "/api/admin/orgs/{id}/invitations", _ID)),
        errors=(DeclaredError(409, "already_member",
                              "l'adresse est déjà celle d'un membre de l'org"),
                DeclaredError(409, "already_invited",
                              "l'adresse a déjà une invitation valide (non expirée, non "
                              "consommée, non révoquée) — `details.invitation` = "
                              "{id, created_at, expires_at}, jamais le code")),
    ),
    Capability(
        key="org.invite.list", handler=_invite_list, Input=InviteListInput,
        authz=ORG_ADMIN_OF("org_id"), Output=Invitations,
        description="List pending invitations for an org you administer.",
        rest=RestBinding("GET", "/api/orgs/{id}/invitations", _ID),
    ),
    Capability(
        key="org.invite.revoke", handler=_invite_revoke, Input=InviteRevokeInput,
        authz=ORG_ADMIN_OF("org_id"), Output=InvitationRevoked,
        description="Revoke a pending invitation.",
        rest=RestBinding("DELETE", "/api/orgs/{id}/invitations/{inv}",
                         {"id": "org_id", "inv": "invite_id"}),
    ),
    Capability(
        key="org.invite.accept", handler=_invite_accept, Input=InviteAcceptInput,
        authz=SUB_ONLY, Output=InvitationAccepted,
        description="Accept an org invitation by mail token or short code. Joins the org.",
        rest=RestBinding("POST", "/api/me/invitations/accept"),
    ),
    Capability(
        key="org.invite.reject", handler=_invite_reject, Input=InviteRejectInput,
        authz=SUB_ONLY, Output=InvitationDeclined,
        # Dans l'ORDRE des gardes du handler.
        errors=(DeclaredError(400, "missing_token",
                              "ni `token` ni `code` n'a été fourni"),
                DeclaredError(410, "invalid_or_expired",
                              "invitation inconnue, expirée, déjà acceptée, ou déjà "
                              "refusée par quelqu'un d'autre"),
                DeclaredError(403, "not_the_invitee",
                              "l'invitation n'est pas adressée à l'adresse de ton "
                              "compte — ou n'est adressée à personne (code anonyme) : "
                              "seule la personne invitée refuse, l'émetteur révoque")),
        description=(
            "Decline an org invitation by mail token or short code — closes it "
            "WITHOUT joining anything. Only the invited address can decline "
            "(unlike accept, which is bearer). Declining never adds or removes a "
            "membership: to leave an org you already belong to, use leave_org."),
        rest=RestBinding("POST", "/api/me/invitations/reject"),
    ),
]
